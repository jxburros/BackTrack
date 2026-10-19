// The BackTrack apps the desktop shell knows, and where to find an installed one.
//
// Each desktop app (BackTrack Surveil, BackTrack Explode) bundles its command-line binary as a
// sidecar and records where it is in <local data>/BackTrack/apps/<id>.json each time it starts.
// The hub reads those records, then falls back to the installers' default locations, so it finds
// an app that was installed but never opened.

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

pub struct AppSpec {
    pub id: &'static str,
    pub name: &'static str, // product name: the installer's folder and the window title
    pub binary: &'static str, // the command-line binary, without .exe
    pub port: u16,          // its default port; Song Deck looks for it there
    pub line: &'static str, // the one plain line on the hub tile
}

pub const APPS: [AppSpec; 2] = [
    AppSpec {
        id: "explode",
        name: "BackTrack Explode",
        binary: "backtrack-explode",
        port: 8827,
        line: "split into stems",
    },
    AppSpec {
        id: "surveil",
        name: "BackTrack Surveil",
        binary: "backtrack-surveil",
        port: 8826,
        line: "turn it into MIDI",
    },
];

pub fn spec(id: &str) -> Option<&'static AppSpec> {
    APPS.iter().find(|a| a.id == id)
}

/// An installed app: its binary and the folders its models are in.
#[derive(Clone, Serialize, Deserialize)]
pub struct Install {
    pub binary: PathBuf,
    pub models: Vec<PathBuf>,
    #[serde(default)]
    pub version: String,
}

pub fn exe_name(binary: &str) -> String {
    if cfg!(windows) {
        format!("{binary}.exe")
    } else {
        binary.to_string()
    }
}

/// <local data>/BackTrack: %LOCALAPPDATA% on Windows, ~/Library/Application Support on macOS,
/// ~/.local/share on Linux.
pub fn shared_dir(local_data: &Path) -> PathBuf {
    local_data.join("BackTrack")
}

/// The models folder every app also reads, so a downloaded model is found by all of them.
pub fn user_models_dir(local_data: &Path) -> PathBuf {
    shared_dir(local_data).join("models")
}

fn record_path(local_data: &Path, id: &str) -> PathBuf {
    shared_dir(local_data)
        .join("apps")
        .join(format!("{id}.json"))
}

pub fn write_record(local_data: &Path, id: &str, install: &Install) {
    let path = record_path(local_data, id);
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    if let Ok(text) = serde_json::to_string_pretty(install) {
        let _ = std::fs::write(path, text);
    }
}

/// Finds an installed app: $BACKTRACK_<ID>_BIN (for development), its record, then the
/// installers' default locations.
pub fn find(spec: &AppSpec, local_data: &Path) -> Option<Install> {
    let env = format!("BACKTRACK_{}_BIN", spec.id.to_uppercase());
    if let Some(bin) = std::env::var_os(&env).map(PathBuf::from) {
        if bin.is_file() {
            let models = bin
                .parent()
                .map(|d| vec![d.join("models")])
                .unwrap_or_default();
            return Some(Install {
                binary: bin,
                models,
                version: String::new(),
            });
        }
    }
    if let Ok(text) = std::fs::read_to_string(record_path(local_data, spec.id)) {
        if let Ok(install) = serde_json::from_str::<Install>(&text) {
            if install.binary.is_file() {
                return Some(install);
            }
        }
    }
    default_locations(spec)
        .into_iter()
        .find(|i| i.binary.is_file())
}

fn default_locations(spec: &AppSpec) -> Vec<Install> {
    let mut out = Vec::new();
    let exe = exe_name(spec.binary);
    let at = |dir: PathBuf, models: PathBuf| Install {
        binary: dir.join(&exe),
        models: vec![models],
        version: String::new(),
    };
    if cfg!(windows) {
        // The NSIS installer: per user under %LOCALAPPDATA%, per machine under Program Files.
        for var in ["LOCALAPPDATA", "ProgramFiles"] {
            if let Some(base) = std::env::var_os(var).map(PathBuf::from) {
                let dir = base.join(spec.name);
                out.push(at(dir.clone(), dir.join("models")));
            }
        }
    } else if cfg!(target_os = "macos") {
        let mut roots = vec![PathBuf::from("/Applications")];
        if let Some(home) = std::env::var_os("HOME").map(PathBuf::from) {
            roots.push(home.join("Applications"));
        }
        for root in roots {
            let contents = root.join(format!("{}.app", spec.name)).join("Contents");
            out.push(at(
                contents.join("MacOS"),
                contents.join("Resources").join("models"),
            ));
        }
    } else {
        // The .deb: binaries in /usr/bin, resources in /usr/lib/<product name>.
        out.push(at(
            PathBuf::from("/usr/bin"),
            PathBuf::from("/usr/lib").join(spec.name).join("models"),
        ));
    }
    out
}
