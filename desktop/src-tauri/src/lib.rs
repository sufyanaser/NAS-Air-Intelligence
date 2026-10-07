mod job;
mod sidecar;

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, RunEvent, State, WindowEvent};

enum SidecarState {
    Starting,
    Running(sidecar::Sidecar),
    Failed(String),
}

struct AppState {
    sidecar: Mutex<SidecarState>,
    /// Bumped on every `agent_watch` call; a running watcher thread checks this and stops
    /// forwarding once it is no longer the current one, instead of leaking threads as the
    /// operator switches which run the UI is looking at.
    watch_generation: AtomicU64,
    /// The run id the frontend is currently showing as active (non-terminal), or None. Lets
    /// the window close handler decide whether quitting needs to ask the operator anything.
    active_run: Mutex<Option<String>>,
    /// Set just before we deliberately stop the sidecar (restart, or app exit) so the
    /// supervisor thread does not treat that expected exit as a crash to recover from.
    sidecar_restarting: AtomicBool,
    /// Assigned to the sidecar process specifically (never to this app process - see job.rs
    /// for why), so the sidecar dies if this app does, without affecting anything else this
    /// app spawns later, such as the auto-updater's installer.
    sidecar_job: job::SidecarJob,
}

fn start_sidecar_and_contain(job: &job::SidecarJob) -> Result<sidecar::Sidecar, String> {
    let sidecar = sidecar::start()?;
    #[cfg(windows)]
    if let Err(e) = job.assign_process(sidecar.raw_handle()) {
        eprintln!("could not contain sidecar in job object: {e}");
    }
    Ok(sidecar)
}

fn current_sidecar(state: &State<'_, AppState>) -> Result<(u16, String), String> {
    match &*state.sidecar.lock().map_err(|_| "state poisoned")? {
        SidecarState::Starting => Err("sidecar is starting".into()),
        SidecarState::Failed(e) => Err(e.clone()),
        SidecarState::Running(s) => Ok((s.port, s.token.clone())),
    }
}

/// One authenticated call to the sidecar. HTTP 200 and 409 ("not finished yet", still a
/// meaningful body) are returned as `Ok`; everything else becomes an `Err` built from the
/// body's `error` field when present.
async fn call_sidecar(
    state: &State<'_, AppState>,
    method: &'static str,
    path: String,
    body: Option<Value>,
) -> Result<Value, String> {
    let (port, token) = current_sidecar(state)?;
    tauri::async_runtime::spawn_blocking(move || {
        let response = sidecar::http_request_with_body(port, method, &path, &token, body.as_ref())?;
        match response.status {
            200 | 202 | 409 => Ok(response.body),
            status => {
                let message = response
                    .body
                    .get("error")
                    .and_then(|v| v.as_str())
                    .unwrap_or("request failed");
                Err(format!("{message} (HTTP {status})"))
            }
        }
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Authenticated loopback health check against the Python sidecar.
#[tauri::command]
async fn sidecar_health(state: State<'_, AppState>) -> Result<Value, String> {
    call_sidecar(&state, "GET", "/health".into(), None).await
}

#[tauri::command]
async fn agent_start(state: State<'_, AppState>, payload: Value) -> Result<Value, String> {
    call_sidecar(&state, "POST", "/agent/start".into(), Some(payload)).await
}

#[tauri::command]
async fn agent_status(state: State<'_, AppState>, run_id: String) -> Result<Value, String> {
    call_sidecar(&state, "GET", format!("/agent/{run_id}/status"), None).await
}

#[tauri::command]
async fn agent_stop(state: State<'_, AppState>, run_id: String) -> Result<Value, String> {
    call_sidecar(&state, "POST", format!("/agent/{run_id}/stop"), None).await
}

#[tauri::command]
async fn agent_result(state: State<'_, AppState>, run_id: String) -> Result<Value, String> {
    call_sidecar(&state, "GET", format!("/agent/{run_id}/result"), None).await
}

#[tauri::command]
async fn agent_timeline(state: State<'_, AppState>, run_id: String) -> Result<Value, String> {
    call_sidecar(&state, "GET", format!("/agent/{run_id}/timeline"), None).await
}

#[tauri::command]
async fn agent_journal(state: State<'_, AppState>, run_id: String) -> Result<Value, String> {
    call_sidecar(&state, "GET", format!("/agent/{run_id}/journal"), None).await
}

#[tauri::command]
async fn agent_programming(state: State<'_, AppState>, run_id: String) -> Result<Value, String> {
    call_sidecar(&state, "GET", format!("/agent/{run_id}/programming"), None).await
}

#[tauri::command]
async fn agent_export(state: State<'_, AppState>, run_id: String) -> Result<Value, String> {
    call_sidecar(&state, "POST", format!("/agent/{run_id}/export/xlsx"), None).await
}

/// Tells Rust whether the frontend currently considers a run "active" (started, not yet
/// terminal). The window close handler uses this - and only this - to decide whether quitting
/// needs to ask the operator anything; Rust never infers it from polling on its own.
#[tauri::command]
fn set_monitoring_active(state: State<'_, AppState>, run_id: Option<String>) -> Result<(), String> {
    *state.active_run.lock().map_err(|_| "state poisoned")? = run_id;
    Ok(())
}

/// "Keep monitoring in background": the window closes and the app exits normally. This is
/// safe because the monitoring worker (and its FFmpeg) is a detached process that does not
/// belong to this app's job object (see job.rs) - it keeps running, and the next launch of
/// this app rediscovers it from the same persistent SQLite state.
#[tauri::command]
fn confirm_exit_keep_monitoring(app: AppHandle) {
    app.exit(0);
}

/// "Stop monitoring and quit": ask the active run to stop gracefully before exiting, so the
/// operator's choice is honoured even though the worker itself would otherwise survive.
#[tauri::command]
async fn confirm_exit_stop_and_quit(
    app: AppHandle,
    state: State<'_, AppState>,
    run_id: String,
) -> Result<(), String> {
    let _ = call_sidecar(&state, "POST", format!("/agent/{run_id}/stop"), None).await;
    app.exit(0);
    Ok(())
}

/// Subscribes the UI to one run's notification-only WebSocket, reconnecting with backoff if
/// the connection drops for any reason other than being superseded by a newer `agent_watch`
/// call or the sidecar being restarted out from under it. Each frame is relayed as an
/// `agent-event` Tauri event; the frontend reacts by re-fetching the REST endpoints above -
/// this channel never carries state the UI could not also get by polling (Phase2.md section
/// 11: "WebSocket is notification only. Persistent backend state is authoritative."). Losing
/// this socket (or never opening it) therefore cannot make monitoring look stopped: the next
/// poll, or the next successful watch, reflects the same backend-authoritative state.
#[tauri::command]
fn agent_watch(app: AppHandle, state: State<'_, AppState>, run_id: String) -> Result<(), String> {
    let generation = state.watch_generation.fetch_add(1, Ordering::SeqCst) + 1;
    std::thread::spawn(move || {
        let mut backoff = Duration::from_millis(500);
        loop {
            let state = app.state::<AppState>();
            if state.watch_generation.load(Ordering::SeqCst) != generation {
                return; // superseded by a newer agent_watch call
            }
            let Ok((port, token)) = current_sidecar(&state) else {
                std::thread::sleep(backoff);
                continue;
            };
            let app_for_events = app.clone();
            let run_id_for_call = run_id.clone();
            let result =
                sidecar::watch_agent_events(port, &token, &run_id_for_call, move |message| {
                    let state = app_for_events.state::<AppState>();
                    if state.watch_generation.load(Ordering::SeqCst) != generation {
                        return false; // superseded by a newer agent_watch call
                    }
                    let parsed: Value = serde_json::from_str(message)
                        .unwrap_or_else(|_| json!({"event": "changed", "raw": message}));
                    let _ = app_for_events.emit("agent-event", parsed);
                    true
                });
            if state.watch_generation.load(Ordering::SeqCst) != generation {
                return;
            }
            match result {
                Ok(()) => return, // the run reached a terminal state; the server closed cleanly
                Err(e) => {
                    let _ = app.emit("agent-event", json!({"event": "watch_retry", "error": e}));
                    std::thread::sleep(backoff);
                    backoff = (backoff * 2).min(Duration::from_secs(10));
                }
            }
        }
    });
    Ok(())
}

/// Watches the sidecar child process and restarts it if it exits unexpectedly (a crash, or
/// being killed from outside the app) - never on a deliberate shutdown, which sets
/// `sidecar_restarting` first so this loop knows to leave it alone.
fn supervise_sidecar(app: AppHandle) {
    loop {
        std::thread::sleep(Duration::from_secs(2));
        let state = app.state::<AppState>();
        if state.sidecar_restarting.load(Ordering::SeqCst) {
            continue;
        }
        let crashed = {
            let Ok(mut guard) = state.sidecar.lock() else {
                continue;
            };
            match &mut *guard {
                SidecarState::Running(s) => !s.is_alive(),
                _ => false,
            }
        };
        if !crashed {
            continue;
        }
        eprintln!("sidecar exited unexpectedly; restarting");
        let _ = app.emit("agent-event", json!({"event": "sidecar_restarting"}));
        let result = start_sidecar_and_contain(&state.sidecar_job);
        if let Ok(mut guard) = state.sidecar.lock() {
            *guard = match result {
                Ok(s) => SidecarState::Running(s),
                Err(e) => SidecarState::Failed(e),
            };
        }
        state.watch_generation.fetch_add(1, Ordering::SeqCst); // old watchers must reconnect
        let _ = app.emit("agent-event", json!({"event": "sidecar_restarted"}));
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Best effort: without it a crash could leave the sidecar running, but the app still works.
    // Created once here and handed into AppState; assigned to the sidecar process specifically
    // once it exists (see start_sidecar_and_contain), never to this process itself.
    let sidecar_job = job::SidecarJob::create().unwrap_or_else(|e| {
        eprintln!("process containment unavailable: {e}");
        job::SidecarJob::disabled()
    });

    // The updater's endpoint list comes entirely from tauri.conf.json's plugins.updater.
    // endpoints (the Rust builder has no endpoint override on this plugin version) - a real
    // update-flow validation therefore builds against a tauri.conf.json temporarily pointed
    // at a local test server, never a runtime override of the shipped production build.
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(AppState {
            sidecar: Mutex::new(SidecarState::Starting),
            watch_generation: AtomicU64::new(0),
            active_run: Mutex::new(None),
            sidecar_restarting: AtomicBool::new(false),
            sidecar_job,
        })
        .invoke_handler(tauri::generate_handler![
            sidecar_health,
            agent_start,
            agent_status,
            agent_stop,
            agent_result,
            agent_timeline,
            agent_journal,
            agent_programming,
            agent_export,
            agent_watch,
            set_monitoring_active,
            confirm_exit_keep_monitoring,
            confirm_exit_stop_and_quit,
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            // Start off the UI thread: the window opens immediately and shows "starting".
            std::thread::spawn(move || {
                let state = handle.state::<AppState>();
                let result = start_sidecar_and_contain(&state.sidecar_job);
                if let Ok(mut guard) = state.sidecar.lock() {
                    *guard = match result {
                        Ok(s) => SidecarState::Running(s),
                        Err(e) => SidecarState::Failed(e),
                    };
                };
            });
            let supervisor_handle = app.handle().clone();
            std::thread::spawn(move || supervise_sidecar(supervisor_handle));
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                let state = window.state::<AppState>();
                let active = state.active_run.lock().ok().and_then(|g| g.clone());
                if let Some(run_id) = active {
                    // Ask the operator what to do instead of closing immediately: Phase2.md
                    // section 10 requires "keep monitoring / stop and quit / cancel", not a
                    // silent exit while a run is active.
                    api.prevent_close();
                    let _ = window.emit("confirm-exit", json!({"run_id": run_id}));
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building NAS Air Intelligence");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            let state = handle.state::<AppState>();
            state.sidecar_restarting.store(true, Ordering::SeqCst);
            if let Ok(mut guard) = state.sidecar.lock() {
                if let SidecarState::Running(s) = &mut *guard {
                    s.shutdown();
                }
            };
        }
    });
}
