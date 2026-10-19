// BackTrack desktop: a native window around a BackTrack app's page, or the hub that holds them all.
//
// One program, built once per product. The bundle identifier decides which it is:
//   com.jxburros.backtrack.surveil   BackTrack Surveil: runs its bundled backtrack-surveil
//   com.jxburros.backtrack.explode   BackTrack Explode: the same, for backtrack-explode
//   com.jxburros.backtrack.hub       BackTrack: finds the installed apps and shows them as tabs
// An app's window starts on start.html while its server comes up, then moves to the server's page.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod apps;
mod server;
mod train;

use apps::{AppSpec, Install, APPS};
use serde::Serialize;
use std::path::PathBuf;
use std::process::Child;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;
use tauri::webview::DownloadEvent;
use tauri::{AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;

const HUB_ID: &str = "hub";

#[derive(Clone, Serialize)]
struct AppStatus {
    id: &'static str,
    name: &'static str,
    line: &'static str,
    state: &'static str, // missing | starting | ready | failed
    url: String,
    error: String,
}

#[derive(Default)]
struct Shared {
    apps: Mutex<Vec<AppStatus>>,
    children: Mutex<Vec<Child>>,
    downloads: Mutex<Vec<(String, String, PathBuf)>>, // (blob URL, file name, temporary file)
}

/// Which product this build is: an app's id, or "hub".
fn mode(app: &AppHandle) -> String {
    let id = &app.config().identifier;
    id.rsplit('.').next().unwrap_or(HUB_ID).to_string()
}

#[tauri::command]
fn status(state: tauri::State<'_, Shared>) -> Vec<AppStatus> {
    state.apps.lock().unwrap().clone()
}

fn set_status(app: &AppHandle, id: &str, state: &'static str, url: String, error: String) {
    let shared = app.state::<Shared>();
    let mut apps = shared.apps.lock().unwrap();
    if let Some(a) = apps.iter_mut().find(|a| a.id == id) {
        a.state = state;
        a.url = url;
        a.error = error;
    }
}

fn status_for(spec: &'static AppSpec, state: &'static str) -> AppStatus {
    AppStatus {
        id: spec.id,
        name: spec.name,
        line: spec.line,
        state,
        url: String::new(),
        error: String::new(),
    }
}

/// Starts the app's server on a thread; `then` runs with its URL once it answers.
fn launch(
    app: &AppHandle,
    spec: &'static AppSpec,
    install: Install,
    then: impl FnOnce(&AppHandle, &str) + Send + 'static,
) {
    let app = app.clone();
    std::thread::spawn(move || {
        let extra = app
            .path()
            .local_data_dir()
            .map(|d| vec![apps::user_models_dir(&d)])
            .unwrap_or_default();
        for dir in &extra {
            let _ = std::fs::create_dir_all(dir);
        }
        match server::start(spec, &install, &extra) {
            Ok(running) => {
                if let Some(child) = running.child {
                    app.state::<Shared>().children.lock().unwrap().push(child);
                }
                set_status(&app, spec.id, "ready", running.url.clone(), String::new());
                then(&app, &running.url);
            }
            Err(e) => set_status(&app, spec.id, "failed", String::new(), e),
        }
    });
}

/// The app's own binary sits next to this program (Contents/MacOS on macOS, /usr/bin from the
/// .deb) and its bundled models are in the resource folder.
fn own_install(app: &AppHandle, spec: &AppSpec) -> Install {
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(PathBuf::from))
        .unwrap_or_default();
    let mut models = vec![exe_dir.join("models")];
    if let Ok(res) = app.path().resource_dir() {
        if res != exe_dir {
            models.insert(0, res.join("models"));
        }
    }
    Install {
        binary: exe_dir.join(apps::exe_name(spec.binary)),
        models,
        version: app.package_info().version.to_string(),
    }
}

static DOWNLOADS: AtomicUsize = AtomicUsize::new(0);

fn download_temp_dir() -> PathBuf {
    std::env::temp_dir().join(format!("backtrack-{}", std::process::id()))
}

/// Downloads from the pages (EXPORT .MID, the JSON) go to a temporary file first, then a save
/// dialog moves them where you choose. (A dialog can't open inside the download callback itself:
/// it would block the webview that is waiting for the answer.)
fn on_download(webview: tauri::Webview, event: DownloadEvent<'_>) -> bool {
    let app = webview.app_handle().clone();
    match event {
        DownloadEvent::Requested { url, destination } => {
            let name = destination
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .filter(|n| !n.is_empty())
                .unwrap_or_else(|| "download".into());
            let dir = download_temp_dir();
            let _ = std::fs::create_dir_all(&dir);
            let n = DOWNLOADS.fetch_add(1, Ordering::Relaxed);
            let temp = dir.join(format!("{n}-{name}"));
            *destination = temp.clone();
            app.state::<Shared>()
                .downloads
                .lock()
                .unwrap()
                .push((url.to_string(), name, temp));
            true
        }
        DownloadEvent::Finished { url, success, .. } => {
            let entry = {
                let shared = app.state::<Shared>();
                let mut list = shared.downloads.lock().unwrap();
                list.iter()
                    .position(|d| d.0 == url.as_str())
                    .map(|i| list.remove(i))
            };
            let Some((_, name, temp)) = entry else {
                return true;
            };
            if !success {
                let _ = std::fs::remove_file(&temp);
                return true;
            }
            let mut dialog = app
                .dialog()
                .file()
                .set_file_name(&name)
                .set_parent(&webview.window());
            if let Ok(dir) = app.path().download_dir() {
                dialog = dialog.set_directory(dir);
            }
            dialog.save_file(move |chosen| {
                if let Some(path) = chosen.and_then(|p| p.into_path().ok()) {
                    // rename fails across drives; copy then.
                    if std::fs::rename(&temp, &path).is_err() && std::fs::copy(&temp, &path).is_ok()
                    {
                        let _ = std::fs::remove_file(&temp);
                    }
                } else {
                    let _ = std::fs::remove_file(&temp);
                }
            });
            true
        }
        _ => true,
    }
}

fn open_window(app: &AppHandle, title: &str, page: &str) -> tauri::Result<tauri::WebviewWindow> {
    WebviewWindowBuilder::new(app, "main", WebviewUrl::App(page.into()))
        .title(title)
        .inner_size(1280.0, 900.0)
        .min_inner_size(720.0, 560.0)
        .disable_drag_drop_handler() // the pages take dropped files themselves
        .on_download(on_download)
        .build()
}

fn setup(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let local_data = app.path().local_data_dir()?;
    let which = mode(app);
    if which == HUB_ID {
        let mut list = Vec::new();
        let mut found = Vec::new();
        for spec in APPS.iter() {
            match apps::find(spec, &local_data) {
                Some(install) => {
                    list.push(status_for(spec, "starting"));
                    found.push((spec, install));
                }
                None => list.push(status_for(spec, "missing")),
            }
        }
        *app.state::<Shared>().apps.lock().unwrap() = list;
        open_window(app, "BackTrack", "hub.html")?;
        for (spec, install) in found {
            launch(app, spec, install, |_, _| {});
        }
    } else {
        let spec = apps::spec(&which)
            .ok_or_else(|| format!("unknown app '{which}' in the bundle identifier"))?;
        let install = own_install(app, spec);
        apps::write_record(&local_data, spec.id, &install);
        *app.state::<Shared>().apps.lock().unwrap() = vec![status_for(spec, "starting")];
        open_window(app, spec.name, "start.html")?;
        launch(app, spec, install, |app, url| {
            if let (Some(w), Ok(u)) = (app.get_webview_window("main"), url.parse::<Url>()) {
                let _ = w.navigate(u);
            }
        });
    }
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .manage(Shared::default())
        .manage(train::Trainer::default())
        .invoke_handler(tauri::generate_handler![
            status,
            train::train_settings,
            train::train_save,
            train::train_pick,
            train::train_check,
            train::train_start,
            train::train_fix,
            train::train_stop,
            train::train_poll,
            train::train_install
        ])
        .setup(|app| setup(app.handle()))
        .build(tauri::generate_context!())
        .expect("error while starting BackTrack")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                train::stop(&app.state::<train::Trainer>());
                for mut child in app.state::<Shared>().children.lock().unwrap().drain(..) {
                    let _ = child.kill();
                    let _ = child.wait();
                }
                let _ = std::fs::remove_dir_all(download_temp_dir());
            }
        });
}
