mod job;
mod sidecar;

use std::sync::Mutex;

use tauri::{Manager, RunEvent, State};

enum SidecarState {
    Starting,
    Running(sidecar::Sidecar),
    Failed(String),
}

struct AppState {
    sidecar: Mutex<SidecarState>,
}

/// Authenticated loopback health check against the Python sidecar.
#[tauri::command]
async fn sidecar_health(state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    let (port, token) = match &*state.sidecar.lock().map_err(|_| "state poisoned")? {
        SidecarState::Starting => return Err("sidecar is starting".into()),
        SidecarState::Failed(e) => return Err(e.clone()),
        SidecarState::Running(s) => (s.port, s.token.clone()),
    };
    tauri::async_runtime::spawn_blocking(move || {
        sidecar::http_request(port, "GET", "/health", &token)
    })
    .await
    .map_err(|e| e.to_string())?
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
        })
        .invoke_handler(tauri::generate_handler![sidecar_health])
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
