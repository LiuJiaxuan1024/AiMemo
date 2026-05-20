use std::time::Duration;

#[tauri::command]
fn open_aimemo(app: tauri::AppHandle) -> Result<(), String> {
    tauri_plugin_opener::OpenerExt::opener(&app)
        .open_url("http://127.0.0.1:8000/app/memo", None::<&str>)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn check_backend_health() -> bool {
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_millis(1200))
        .build()
    {
        Ok(client) => client,
        Err(_) => return false,
    };

    client
        .get("http://127.0.0.1:8000/api/health")
        .send()
        .map(|response| response.status().is_success())
        .unwrap_or(false)
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![open_aimemo, check_backend_health])
        .run(tauri::generate_context!())
        .expect("failed to run Memo Elf desktop app");
}
