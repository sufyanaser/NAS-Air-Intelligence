mod job;
mod sidecar;

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, RunEvent, State};

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
            200 | 409 => Ok(response.body),
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

/// Subscribes the UI to one run's notification-only WebSocket. Each frame is relayed as an
/// `agent-event` Tauri event; the frontend reacts by re-fetching the REST endpoints above -
/// this channel never carries state the UI could not also get by polling (Phase2.md section
/// 11: "WebSocket is notification only. Persistent backend state is authoritative."). Losing
/// this socket (or never opening it) therefore cannot make monitoring look stopped: the next
/// poll, or the next successful watch, reflects the same backend-authoritative state.
#[tauri::command]
fn agent_watch(app: AppHandle, state: State<'_, AppState>, run_id: String) -> Result<(), String> {
    let (port, token) = current_sidecar(&state)?;
    let generation = state.watch_generation.fetch_add(1, Ordering::SeqCst) + 1;
    std::thread::spawn(move || {
        let app_for_events = app.clone();
        let result = sidecar::watch_agent_events(port, &token, &run_id, move |message| {
            let state = app_for_events.state::<AppState>();
            if state.watch_generation.load(Ordering::SeqCst) != generation {
                return false; // superseded by a newer agent_watch call
            }
            let parsed: Value = serde_json::from_str(message)
                .unwrap_or_else(|_| json!({"event": "changed", "raw": message}));
            let _ = app_for_events.emit("agent-event", parsed);
            true
        });
        if let Err(e) = result {
            let _ = app.emit("agent-event", json!({"event": "watch_error", "error": e}));
        }
    });
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Best effort: without it a crash could leave the sidecar running, but the app still works.
    if let Err(e) = job::contain_process_tree() {
        eprintln!("process containment unavailable: {e}");
    }

    let app = tauri::Builder::default()
        .manage(AppState {
            sidecar: Mutex::new(SidecarState::Starting),
            watch_generation: AtomicU64::new(0),
        })
        .invoke_handler(tauri::generate_handler![
            sidecar_health,
            agent_start,
            agent_status,
            agent_stop,
            agent_result,
            agent_timeline,
            agent_journal,
            agent_watch,
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            // Start off the UI thread: the window opens immediately and shows "starting".
            std::thread::spawn(move || {
                let result = sidecar::start();
                let state = handle.state::<AppState>();
                if let Ok(mut guard) = state.sidecar.lock() {
                    *guard = match result {
                        Ok(s) => SidecarState::Running(s),
                        Err(e) => SidecarState::Failed(e),
                    };
                };
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building NAS Air Intelligence");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            let state = handle.state::<AppState>();
            if let Ok(mut guard) = state.sidecar.lock() {
                if let SidecarState::Running(s) = &mut *guard {
                    s.shutdown();
                }
            };
        }
    });
}
