// The hub's TRAIN view: runs the training pipeline (training/pipeline.py, bundled with the hub as
// a resource) for the drum, bass or melody model, with its settings, a setup check, the run's
// progress, and INSTALL IN SONG DECK for the best model (a Song Deck model plugin).
//
// The pipeline needs things the hub can't ship: Python with PyTorch, a Song Deck checkout (it
// renders the training songs) and, for ACE-Step performances, an ACE-Step 1.5 install. The setup
// check finds them and says what's missing; the fixes it can do itself (Song Deck's npm install,
// its sound bank) run here like a run does, with their output in the log.

use crate::apps;
use serde::{Deserialize, Serialize};
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::DialogExt;

const MAX_LINES: usize = 4000;
const STEPS: [&str; 7] = ["render", "perform", "libraries", "train", "evaluate", "parity", "report"];

#[derive(Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    pub songdeck: String,
    pub acestep: String, // ACE-Step 1.5 folder: the hub starts `uv run acestep-api` there
    pub use_acestep: bool,
    pub python: String, // empty: found on PATH
    pub data: String,   // songs, feature cache and runs
    pub songs: u32,
    pub epochs: u32,
    pub device: String, // auto | cuda | mps | cpu
    pub kind: String,   // what to train: drums | bass | melody
    pub queue: Vec<String>, // kinds to train one after another (ONE AFTER ANOTHER)
    pub start_at: String,   // "HH:MM" for the queue to wait for, or empty to start now
    pub libraries: Vec<String>, // outside datasets to train on too (training/libraries.py ids)
    pub augment: Vec<String>, // degradation kinds (training/augment.py) for degraded copies; empty: off
    pub augment_copies: u32,  // degraded copies of each training item, 1-4
    pub augment_noise: String, // a folder of your own noise recordings (WAV), or empty
    // The version written into each kind's model (general.version, the file name), "x.y.z";
    // missing or empty: the next minor after the released model (`suggested_version`).
    pub versions: std::collections::BTreeMap<String, String>,
}

impl Default for Settings {
    fn default() -> Self {
        let home = home();
        Settings {
            songdeck: find_songdeck().map(path_str).unwrap_or_default(),
            acestep: find_acestep().map(path_str).unwrap_or_default(),
            use_acestep: true,
            python: String::new(),
            data: home
                .join("backtrack-training")
                .to_string_lossy()
                .into_owned(),
            songs: 400,
            epochs: 30,
            device: "auto".into(),
            kind: "drums".into(),
            queue: vec!["drums".into(), "bass".into(), "melody".into()],
            start_at: String::new(),
            libraries: Vec::new(),
            augment: Vec::new(),
            augment_copies: 1,
            augment_noise: String::new(),
            versions: std::collections::BTreeMap::new(),
        }
    }
}

/// A command built when its turn comes, so it can use what the step before it installed.
type Step = Box<dyn FnOnce() -> Result<Command, String> + Send>;

#[derive(Default)]
struct Job {
    child: Option<Child>,
    queue: Vec<Step>, // what runs next, one after another, while each one succeeds
    kind: String, // run | resume | queue | songdeck-install | soundbank | uv-install | python-install
    lines: Vec<String>,
    dropped: usize, // lines dropped from the front, so offsets stay stable
    exit: Option<i32>,
}

#[derive(Default)]
pub struct Trainer {
    job: Arc<Mutex<Job>>,
}

fn home() -> PathBuf {
    std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
        .map(PathBuf::from)
        .unwrap_or_default()
}

fn expand(p: &str) -> PathBuf {
    match p.strip_prefix("~") {
        Some(rest) => home().join(rest.trim_start_matches(['/', '\\'])),
        None => PathBuf::from(p),
    }
}

fn settings_path(app: &AppHandle) -> Option<PathBuf> {
    let d = app.path().local_data_dir().ok()?;
    Some(apps::shared_dir(&d).join("training").join("settings.json"))
}

/// The training code shipped with the hub: <resources>/trainer/training/pipeline.py.
fn trainer_dir(app: &AppHandle) -> Option<PathBuf> {
    if let Some(dev) = std::env::var_os("BACKTRACK_TRAINER_DIR").map(PathBuf::from) {
        return Some(dev);
    }
    let res = app.path().resource_dir().ok()?;
    Some(res.join("trainer"))
}

/// `%NAME%` references in a Windows PATH, expanded from the environment (unknown names stay).
#[cfg_attr(not(windows), allow(dead_code))]
fn expand_vars(s: &str) -> String {
    let mut out = String::new();
    let mut rest = s;
    while let Some(i) = rest.find('%') {
        out.push_str(&rest[..i]);
        let tail = &rest[i + 1..];
        match tail.find('%') {
            Some(j) if j > 0 => match std::env::var(&tail[..j]) {
                Ok(v) => {
                    out.push_str(&v);
                    rest = &tail[j + 1..];
                }
                Err(_) => {
                    out.push('%');
                    rest = tail;
                }
            },
            _ => {
                out.push('%');
                rest = tail;
            }
        }
    }
    out.push_str(rest);
    out
}

/// The value in `reg query <key> /v Path` output: `    Path    REG_EXPAND_SZ    C:\a;C:\b`.
#[cfg_attr(not(windows), allow(dead_code))]
fn reg_value(out: &str) -> String {
    out.lines()
        .find_map(|l| {
            let at = l.find("REG_EXPAND_SZ").map(|i| i + 13).or_else(|| l.find("REG_SZ").map(|i| i + 6))?;
            Some(l[at..].trim().to_string())
        })
        .unwrap_or_default()
}

/// A desktop app on macOS or Linux starts with a bare PATH, without Homebrew, ~/.local/bin and
/// the like; the login shell's PATH has what a terminal would find (node, npm, uv, python).
/// On Windows the app keeps the PATH it was started with, so a Python or uv installed since
/// is missing from it: the registry's PATH (user and system) and the folders those installers
/// use are added.
fn search_path() -> String {
    static PATH: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    PATH.get_or_init(|| {
        let mut parts: Vec<String> = Vec::new();
        let mut add = |s: &str| {
            for p in std::env::split_paths(s) {
                let p = p.to_string_lossy().into_owned();
                if !p.is_empty() && !parts.contains(&p) {
                    parts.push(p);
                }
            }
        };
        #[cfg(not(windows))]
        {
            let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/sh".into());
            if let Ok(out) = Command::new(shell)
                .args(["-l", "-c", "printf %s \"$PATH\""])
                .stdin(Stdio::null())
                .stderr(Stdio::null())
                .output()
            {
                add(&String::from_utf8_lossy(&out.stdout));
            }
            let h = home();
            for extra in [
                "/opt/homebrew/bin".into(),
                "/usr/local/bin".into(),
                h.join(".local/bin").to_string_lossy().into_owned(),
                h.join(".cargo/bin").to_string_lossy().into_owned(),
            ] {
                add(&extra);
            }
        }
        add(&std::env::var("PATH").unwrap_or_default());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            let system = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into());
            let reg = PathBuf::from(system).join("System32").join("reg.exe");
            for key in [
                r"HKCU\Environment",
                r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            ] {
                // Not command(): that would wait on this very PATH.
                if let Ok(out) = Command::new(&reg)
                    .args(["query", key, "/v", "Path"])
                    .stdin(Stdio::null())
                    .stderr(Stdio::null())
                    .creation_flags(0x0800_0000)
                    .output()
                {
                    add(&expand_vars(&reg_value(&String::from_utf8_lossy(&out.stdout))));
                }
            }
            let h = home();
            add(&h.join(".local").join("bin").to_string_lossy());
            add(&h.join(".cargo").join("bin").to_string_lossy());
            // python.org's installer without "Add to PATH", and the Microsoft Store's shims.
            let local = std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_default();
            let mut homes = vec![local.join("Programs").join("Python")];
            if let Some(pf) = std::env::var_os("ProgramFiles") {
                homes.push(PathBuf::from(pf));
            }
            for base in homes {
                for e in std::fs::read_dir(base).into_iter().flatten().flatten() {
                    let name = e.file_name().to_string_lossy().to_lowercase();
                    if name.starts_with("python3") && e.path().join("python.exe").is_file() {
                        add(&e.path().to_string_lossy());
                    }
                }
            }
            add(&local.join("Microsoft").join("WindowsApps").to_string_lossy());
        }
        std::env::join_paths(parts.iter())
            .map(|p| p.to_string_lossy().into_owned())
            .unwrap_or_default()
    })
    .clone()
}

fn which(name: &str) -> Option<PathBuf> {
    let exts: &[&str] = if cfg!(windows) {
        &[".exe", ".cmd", ".bat", ""]
    } else {
        &[""]
    };
    for dir in std::env::split_paths(&search_path()) {
        for ext in exts {
            let p = dir.join(format!("{name}{ext}"));
            if p.is_file() {
                return Some(p);
            }
        }
    }
    None
}

fn command(program: impl AsRef<std::ffi::OsStr>) -> Command {
    let mut c = Command::new(program);
    c.env("PATH", search_path()).stdin(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        c.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    c
}

// ---------------------------------------------------------------------------- settings

#[tauri::command]
pub fn train_settings(app: AppHandle) -> Settings {
    settings_path(&app)
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_default()
}

#[tauri::command]
pub fn train_save(app: AppHandle, settings: Settings) -> Result<(), String> {
    let p = settings_path(&app).ok_or("no folder for the settings")?;
    if let Some(d) = p.parent() {
        std::fs::create_dir_all(d).map_err(|e| e.to_string())?;
    }
    let text = serde_json::to_string_pretty(&settings).map_err(|e| e.to_string())?;
    std::fs::write(p, text).map_err(|e| e.to_string())
}

/// A folder (or, with `file`, a file) chosen in the system dialog.
#[tauri::command]
pub async fn train_pick(app: AppHandle, title: String, file: bool) -> Option<String> {
    let d = app.dialog().file().set_title(title);
    let picked = if file {
        d.blocking_pick_file()
    } else {
        d.blocking_pick_folder()
    };
    picked
        .and_then(|p| p.into_path().ok())
        .map(|p| p.to_string_lossy().into_owned())
}

// ---------------------------------------------------------------------------- detection

fn path_str(p: PathBuf) -> String {
    p.to_string_lossy().into_owned()
}

/// Where people keep checkouts and downloads, most likely first.
fn roots() -> Vec<PathBuf> {
    let h = home();
    let mut v = vec![h.clone()];
    for n in ["Documents", "Downloads", "Desktop", "src", "code", "repos", "dev", "projects", "GitHub", "source/repos", "Documents/GitHub"] {
        v.push(h.join(n));
    }
    for n in ["OneDrive", "OneDrive - Personal"] {
        for sub in ["Documents", "Desktop", "Downloads"] {
            v.push(h.join(n).join(sub));
        }
    }
    v.retain(|p| p.is_dir());
    v
}

/// Every folder up to `depth` levels under the usual roots (a download unzips to
/// `Name-main/Name-main`), skipping what never holds a project.
fn candidate_dirs(depth: u32) -> Vec<PathBuf> {
    fn walk(dir: &std::path::Path, depth: u32, out: &mut Vec<PathBuf>, budget: &mut u32) {
        if depth == 0 || *budget == 0 {
            return;
        }
        for e in std::fs::read_dir(dir).into_iter().flatten().flatten() {
            let name = e.file_name().to_string_lossy().to_lowercase();
            if name.starts_with('.')
                || matches!(name.as_str(), "node_modules" | "appdata" | "library" | "target" | "venv" | "__pycache__")
                || !e.path().is_dir()
            {
                continue;
            }
            *budget = budget.saturating_sub(1);
            out.push(e.path());
            walk(&e.path(), depth - 1, out, budget);
        }
    }
    let mut out = Vec::new();
    let mut budget = 6000;
    for r in roots() {
        // The home folder itself is wide: one level there, deeper in the folders below it.
        walk(&r, if r == home() { 1 } else { depth }, &mut out, &mut budget);
    }
    let mut seen = std::collections::HashSet::new();
    out.retain(|p| seen.insert(p.clone()));
    out
}

fn is_songdeck(d: &std::path::Path) -> bool {
    std::fs::read_to_string(d.join("package.json")).is_ok_and(|t| t.contains("dataset:render"))
}

fn is_acestep(d: &std::path::Path) -> bool {
    std::fs::read_to_string(d.join("pyproject.toml")).is_ok_and(|t| t.contains("acestep-api"))
}

/// A Song Deck checkout (the one with `dataset:render`), preferring one that is set up.
fn find_songdeck() -> Option<PathBuf> {
    let found: Vec<PathBuf> = candidate_dirs(3).into_iter().filter(|d| is_songdeck(d)).collect();
    found.iter().find(|d| d.join("node_modules").is_dir()).or(found.first()).cloned()
}

fn find_acestep() -> Option<PathBuf> {
    let found: Vec<PathBuf> = candidate_dirs(3).into_iter().filter(|d| is_acestep(d)).collect();
    found.iter().find(|d| d.join(".venv").is_dir()).or(found.first()).cloned()
}

/// The settings with what's missing or wrong found automatically: a Song Deck or ACE-Step
/// folder that isn't set (or isn't there any more) is looked for, so nobody has to.
fn resolved(s: &Settings) -> Settings {
    let mut s = s.clone();
    if !is_songdeck(&expand(&s.songdeck)) {
        if let Some(p) = find_songdeck() {
            s.songdeck = path_str(p);
        }
    }
    if s.use_acestep && !expand(&s.acestep).is_dir() {
        if let Some(p) = find_acestep() {
            s.acestep = path_str(p);
        }
    }
    s
}

// ---------------------------------------------------------------------------- setup check

#[derive(Serialize)]
pub struct Check {
    name: &'static str,
    state: &'static str, // ok | warn | fail
    detail: String,
    fix: Option<&'static str>, // a job the hub can run: songdeck-install | soundbank
}

// The probe leaves out torch, cuda and mps when PyTorch won't import: a Python without it
// is still a Python, and the check names what to install.
#[derive(Serialize, Deserialize, Default)]
#[serde(default)]
struct PythonInfo {
    exe: String,
    version: String,
    torch: Option<String>,
    cuda: bool,
    mps: bool,
    error: Option<String>,
}

const PROBE: &str = "import json,sys\nr={'exe':sys.executable,'version':sys.version.split()[0]}\ntry:\n import numpy,torch\n r['torch']=torch.__version__\n r['cuda']=bool(torch.cuda.is_available())\n m=getattr(torch.backends,'mps',None)\n r['mps']=bool(m and m.is_available())\nexcept Exception as e:\n r['error']=str(e)\nprint(json.dumps(r))";

/// A way to start a Python: the program and the arguments that pick the version.
struct Cand {
    label: String,
    prog: PathBuf,
    args: Vec<String>,
}

fn venv_python(dir: &std::path::Path) -> PathBuf {
    if cfg!(windows) {
        dir.join("Scripts").join("python.exe")
    } else {
        dir.join("bin").join("python")
    }
}

/// Every Python worth asking, the likeliest to have PyTorch first: the one chosen in the
/// settings, the training venv from the guide, ACE-Step's own environment, conda, then the
/// Pythons on PATH (a specific version before "the newest", which may be too new for PyTorch).
fn python_candidates(s: &Settings) -> Vec<Cand> {
    let mut v: Vec<Cand> = Vec::new();
    let mut add_path = |label: String, prog: PathBuf| {
        if prog.is_file() && !v.iter().any(|c| c.prog == prog && c.args.is_empty()) {
            v.push(Cand { label, prog, args: vec![] });
        }
    };
    if !s.python.trim().is_empty() {
        let c = s.python.trim().to_string();
        let prog = if c.contains('/') || c.contains('\\') { Some(expand(&c)) } else { which(&c) };
        if let Some(p) = prog {
            add_path(c, p);
        }
    }
    let h = home();
    let mut venvs = vec![h.join("backtrack-training").join("venv")];
    if !s.data.trim().is_empty() {
        venvs.push(expand(&s.data).join("venv"));
    }
    if !s.acestep.trim().is_empty() {
        venvs.push(expand(&s.acestep).join(".venv"));
    }
    for d in venvs {
        add_path(format!("{}", d.display()), venv_python(&d));
    }
    for base in ["miniconda3", "anaconda3", "miniforge3", "mambaforge", "micromamba"] {
        for root in [h.clone(), h.join("AppData").join("Local"), PathBuf::from("C:/ProgramData")] {
            let r = root.join(base);
            add_path(format!("conda {base}"), if cfg!(windows) { r.join("python.exe") } else { r.join("bin").join("python") });
            for e in std::fs::read_dir(r.join("envs")).into_iter().flatten().flatten() {
                add_path(format!("conda {base}/{}", e.file_name().to_string_lossy()), venv_python(&e.path()));
                if cfg!(windows) {
                    add_path(format!("conda {base}/{}", e.file_name().to_string_lossy()), e.path().join("python.exe"));
                }
            }
        }
    }
    if s.python.trim().is_empty() {
        let launcher = which("py");
        if cfg!(windows) {
            if let Some(py) = launcher {
                for ver in ["3.12", "3.11", "3.13", "3.10"] {
                    v.push(Cand { label: format!("py -{ver}"), prog: py.clone(), args: vec![format!("-{ver}")] });
                }
                v.push(Cand { label: "py".into(), prog: py, args: vec![] });
            }
        }
        for name in ["python3.12", "python3.11", "python3.13", "python3.10", "python3", "python"] {
            if let Some(p) = which(name) {
                if !v.iter().any(|c| c.prog == p && c.args.is_empty()) {
                    v.push(Cand { label: name.into(), prog: p, args: vec![] });
                }
            }
        }
    }
    v
}

fn minor(version: &str) -> u32 {
    version.split('.').nth(1).and_then(|m| m.parse().ok()).unwrap_or(0)
}

fn probe_python(s: &Settings) -> Result<PythonInfo, String> {
    let mut tried = Vec::new();
    let mut best: Option<PythonInfo> = None;
    let mut seen: Vec<String> = Vec::new();
    for c in python_candidates(s) {
        let out = command(&c.prog).args(&c.args).args(["-c", PROBE]).output();
        let info = out.ok().and_then(|o| serde_json::from_slice::<PythonInfo>(&o.stdout).ok());
        let Some(info) = info else {
            tried.push(c.label);
            continue;
        };
        if seen.contains(&info.exe) {
            continue;
        }
        seen.push(info.exe.clone());
        if info.torch.is_some() {
            return Ok(info);
        }
        // Without PyTorch anywhere, name a Python PyTorch has builds for rather than the newest.
        let ok_ver = |i: &PythonInfo| (10..=13).contains(&minor(&i.version));
        if best.as_ref().is_none_or(|b| ok_ver(&info) && !ok_ver(b)) {
            best = Some(info);
        }
    }
    best.ok_or_else(|| {
        if tried.is_empty() {
            "no Python found on this computer".to_string()
        } else {
            format!("no working Python found (tried {})", tried.join(", "))
        }
    })
}

fn acestep_up() -> bool {
    std::net::TcpStream::connect_timeout(&([127, 0, 0, 1], 8001).into(), Duration::from_millis(300))
        .is_ok()
}

#[tauri::command]
pub async fn train_check(app: AppHandle, settings: Settings) -> Vec<Check> {
    let s = resolved(&settings);
    let found = |was: &str, now: &str| -> &'static str {
        if was.trim() != now.trim() { " (found automatically)" } else { "" }
    };
    let mut out = Vec::new();
    let mut push = |name, state, detail: String, fix| {
        out.push(Check {
            name,
            state,
            detail,
            fix,
        })
    };

    match trainer_dir(&app) {
        Some(d) if d.join("training").join("pipeline.py").is_file() => {
            push("Training code", "ok", "comes with BackTrack".into(), None)
        }
        _ => push(
            "Training code",
            "fail",
            "missing from this install: reinstall BackTrack".into(),
            None,
        ),
    }

    let local = app.path().local_data_dir().unwrap_or_default();
    match apps::spec("surveil").and_then(|spec| apps::find(spec, &local)) {
        Some(i) => push("BackTrack Surveil", "ok", i.binary.display().to_string(), None),
        None => push("BackTrack Surveil", "fail",
            "not installed: training checks every model with it. Install BackTrack Surveil, open it once, then check again.".into(), None),
    }

    match probe_python(&s) {
        Ok(p) if p.torch.is_some() => {
            let gpu = if p.cuda { "NVIDIA GPU (CUDA)" } else if p.mps { "Apple GPU (MPS)" } else { "no GPU: trains on the CPU (slow)" };
            let state = if p.cuda || p.mps { "ok" } else { "warn" };
            let auto = if s.python.trim().is_empty() { " (found automatically)" } else { "" };
            push("Python and PyTorch", state, format!("Python {} with PyTorch {}, {gpu}: {}{auto}", p.version, p.torch.unwrap_or_default(), p.exe), None);
        }
        Ok(p) if minor(&p.version) > 13 => push("Python and PyTorch", "fail", format!(
            "the Python here is {} ({}), which PyTorch has no release for yet. Install puts Python 3.12 with PyTorch beside it, for the training only (a few GB).",
            p.version, p.exe), Some("python-install")),
        Ok(p) => push("Python and PyTorch", "fail", format!(
            "Python {} at {} can't load PyTorch ({}). Install puts Python 3.12 with PyTorch beside it, for the training only (a few GB), or choose another Python above.",
            p.version, p.exe, p.error.unwrap_or_default()), Some("python-install")),
        Err(e) => push("Python and PyTorch", "fail",
            format!("{e}. Install puts Python 3.12 with PyTorch in {}, for the training only (a few GB), or choose your Python above.", expand(&s.data).join("venv").display()), Some("python-install")),
    }

    let npm = which(if cfg!(windows) { "npm.cmd" } else { "npm" }).or_else(|| which("npm"));
    match &npm {
        Some(p) => push("Node.js", "ok", p.display().to_string(), None),
        None => push(
            "Node.js",
            "fail",
            "npm not found: install Node.js 20 or later (nodejs.org), then check again.".into(),
            None,
        ),
    }

    let sd = expand(&s.songdeck);
    let pkg = std::fs::read_to_string(sd.join("package.json")).ok();
    if s.songdeck.trim().is_empty() || pkg.is_none() {
        push(
            "Song Deck",
            "fail",
            "no Song Deck folder found in your home, Documents, Downloads or Desktop: choose it above (the one with package.json).".into(),
            None,
        );
    } else if !pkg.unwrap_or_default().contains("dataset:render") {
        push(
            "Song Deck",
            "fail",
            format!(
                "{} is too old for training: update it (GitHub Desktop: Fetch, then Pull).",
                sd.display()
            ),
            None,
        );
    } else if !sd.join("node_modules").is_dir() {
        push(
            "Song Deck",
            "fail",
            "its dependencies aren't installed.".into(),
            Some("songdeck-install"),
        );
    } else {
        push("Song Deck", "ok", format!("{}{}", sd.display(), found(&settings.songdeck, &s.songdeck)), None);
        if sd.join("soundbank").is_dir() {
            push(
                "Sound bank",
                "ok",
                "recorded instruments for the training songs".into(),
                None,
            );
        } else {
            push(
                "Sound bank",
                "warn",
                "not built: drums would all be synthesized. Build it (a few minutes, about 60 MB)."
                    .into(),
                Some("soundbank"),
            );
        }
    }

    if s.use_acestep {
        if acestep_up() {
            push(
                "ACE-Step",
                "ok",
                "running at http://127.0.0.1:8001".into(),
                None,
            );
        } else if s.acestep.trim().is_empty() || !expand(&s.acestep).is_dir() {
            push("ACE-Step", "fail", "no ACE-Step 1.5 folder found in your home, Documents, Downloads or Desktop: choose it above (BackTrack starts it for each run), start `acestep-api` yourself, or untick Use ACE-Step.".into(), None);
        } else if which("uv").is_none() {
            push("ACE-Step", "fail", "uv not found, which starts ACE-Step: Install gets it from docs.astral.sh/uv.".into(), Some("uv-install"));
        } else {
            push(
                "ACE-Step",
                "ok",
                format!(
                    "BackTrack starts it in {} for each run and stops it before training{}",
                    expand(&s.acestep).display(),
                    found(&settings.acestep, &s.acestep)
                ),
                None,
            );
        }
    }

    // Worse recordings (train.augment): ffmpeg for the MP3 and Opus copies, the noise folder.
    let kinds = augment_kinds(&s);
    if !kinds.is_empty() {
        let copies = s.augment_copies.clamp(1, 4);
        let what = format!(
            "{copies} degraded {} of each training song ({})",
            if copies == 1 { "copy" } else { "copies" },
            kinds.join(", ")
        );
        match augment_noise(&s) {
            Some(d) if !d.is_dir() => push(
                "Worse recordings",
                "fail",
                format!("no folder {}: choose your noise recordings again, or leave it empty", d.display()),
                None,
            ),
            _ if kinds.contains(&"codec") && which("ffmpeg").is_none() => push(
                "Worse recordings",
                "warn",
                format!("{what}. ffmpeg not found: the MP3 and Opus copies are skipped, the rest run (install ffmpeg to have them)."),
                None,
            ),
            _ => push("Worse recordings", "ok", what, None),
        }
    }

    // Model version: the kind(s) about to train, never a released version with other weights.
    let current = kind(&s);
    let kinds = std::iter::once(current)
        .chain(KINDS.into_iter().filter(|k| *k != current && s.queue.iter().any(|q| q == k)));
    for k in kinds {
        let ks = Settings { kind: k.into(), ..s.clone() };
        let released = released_versions(&app, k);
        match model_version(&app, &ks) {
            Err(e) => push("Model version", "fail", e, None),
            Ok(v) if released.contains(&parse_version(&v).unwrap_or_default()) => push(
                "Model version",
                "warn",
                format!("surveil-{k}-{v} is already a released model with other weights: choose a newer version ({} is next)", suggested_version(&app, k)),
                None,
            ),
            Ok(v) => push("Model version", "ok", format!("surveil-{k}-{v}.gguf"), None),
        }
    }
    out
}

// ---------------------------------------------------------------------------- jobs

fn spawn(state: &Trainer, kind: &str, cmd: Command) -> Result<(), String> {
    spawn_chain(state, kind, vec![Box::new(move || Ok(cmd))])
}

fn spawn_chain(state: &Trainer, kind: &str, steps: Vec<Step>) -> Result<(), String> {
    let mut job = state.job.lock().unwrap();
    if let Some(c) = job.child.as_mut() {
        if c.try_wait().ok().flatten().is_none() {
            return Err("something is already running: stop it first".into());
        }
    }
    *job = Job {
        kind: kind.into(),
        queue: steps,
        ..Job::default()
    };
    if let Err(e) = next_step(&mut job, &state.job) {
        job.queue.clear();
        return Err(e);
    }
    Ok(())
}

/// Starts the next queued command, its output going to the job's log.
fn next_step(job: &mut Job, shared: &Arc<Mutex<Job>>) -> Result<(), String> {
    if job.queue.is_empty() {
        return Ok(());
    }
    let mut cmd = job.queue.remove(0)()?;
    if job.kind != "run" && job.kind != "resume" {
        let args: Vec<_> = cmd.get_args().map(|a| a.to_string_lossy().into_owned()).collect();
        job.lines.push(format!("> {} {}", cmd.get_program().to_string_lossy(), args.join(" ")));
    }
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0); // STOP reaches everything it started
    }
    let mut child = cmd.spawn().map_err(|e| format!("couldn't start: {e}"))?;
    let shared = shared.clone();
    for stream in [
        child
            .stdout
            .take()
            .map(|s| Box::new(s) as Box<dyn std::io::Read + Send>),
        child
            .stderr
            .take()
            .map(|s| Box::new(s) as Box<dyn std::io::Read + Send>),
    ]
    .into_iter()
    .flatten()
    {
        let shared = shared.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stream).split(b'\n').map_while(Result::ok) {
                let text = String::from_utf8_lossy(&line)
                    .trim_end_matches('\r')
                    .to_string();
                // Progress bars rewrite one line with \r: keep the last state.
                let text = text.rsplit('\r').next().unwrap_or("").to_string();
                let mut j = shared.lock().unwrap();
                j.lines.push(text);
                if j.lines.len() > MAX_LINES {
                    let cut = j.lines.len() - MAX_LINES;
                    j.lines.drain(..cut);
                    j.dropped += cut;
                }
            }
        });
    }
    job.child = Some(child);
    Ok(())
}

/// Each kind of model has its own songs and runs; the drum model keeps the folders it always had.
fn kind(s: &Settings) -> &'static str {
    match s.kind.as_str() {
        "bass" => "bass",
        "melody" => "melody",
        _ => "drums",
    }
}

fn dataset_dir(s: &Settings) -> PathBuf {
    expand(&s.data).join(format!("sd-{}", kind(s)))
}

fn runs_dir(s: &Settings) -> PathBuf {
    match kind(s) {
        "drums" => expand(&s.data).join("runs"),
        k => expand(&s.data).join(format!("runs-{k}")),
    }
}

fn config_path(s: &Settings) -> PathBuf {
    match kind(s) {
        "drums" => expand(&s.data).join("pipeline.json"),
        k => expand(&s.data).join(format!("pipeline-{k}.json")),
    }
}

/// "x.y.z" as numbers, or None for anything else (a model version must be plain semver: it is
/// the GGUF's general.version and part of the file name).
fn parse_version(v: &str) -> Option<(u32, u32, u32)> {
    let mut parts = v.trim().split('.').map(|p| {
        (!p.is_empty() && p.bytes().all(|b| b.is_ascii_digit())).then(|| p.parse::<u32>().ok()).flatten()
    });
    let (a, b, c) = (parts.next()??, parts.next()??, parts.next()??);
    parts.next().is_none().then_some((a, b, c))
}

/// The released versions of a kind's model: the `surveil-<kind>-x.y.z.gguf` files that come with
/// the hub (trainer/weights, copied from the repository's weights/), newest last.
fn released_versions(app: &AppHandle, kind: &str) -> Vec<(u32, u32, u32)> {
    let prefix = format!("surveil-{kind}-");
    let mut out: Vec<(u32, u32, u32)> = trainer_dir(app)
        .and_then(|d| std::fs::read_dir(d.join("weights")).ok())
        .into_iter()
        .flatten()
        .filter_map(|e| {
            let name = e.ok()?.file_name().into_string().ok()?;
            parse_version(name.strip_prefix(&prefix)?.strip_suffix(".gguf")?)
        })
        .collect();
    out.sort_unstable();
    out
}

/// The version a new model of this kind gets unless you choose one: the next minor after the
/// newest released one, so it never reuses a released version for different weights; 0.1.0
/// when none is released.
fn suggested_version(app: &AppHandle, kind: &str) -> String {
    next_version(&released_versions(app, kind))
}

/// The next minor after the newest of `released` (sorted), or 0.1.0.
fn next_version(released: &[(u32, u32, u32)]) -> String {
    match released.last() {
        Some((major, minor, _)) => format!("{major}.{}.0", minor + 1),
        None => "0.1.0".into(),
    }
}

/// The version this run writes into the model: the one chosen for the kind, or the suggestion.
/// It is part of the pipeline's fingerprint, so it stays the same between runs unless changed.
fn model_version(app: &AppHandle, s: &Settings) -> Result<String, String> {
    match s.versions.get(kind(s)).map(|v| v.trim()).filter(|v| !v.is_empty()) {
        None => Ok(suggested_version(app, kind(s))),
        Some(v) if parse_version(v).is_some() => Ok(v.to_string()),
        Some(v) => Err(format!("the {} model's version {v:?} is not like 1.2.0: three numbers with dots", kind(s))),
    }
}

/// For the page: each kind's newest released version and the suggested next one.
#[tauri::command]
pub fn train_versions(app: AppHandle) -> serde_json::Value {
    let mut out = serde_json::Map::new();
    for k in KINDS {
        let released = released_versions(&app, k).last().map(|(a, b, c)| format!("{a}.{b}.{c}"));
        out.insert(k.into(), serde_json::json!({ "released": released, "suggested": suggested_version(&app, k) }));
    }
    out.into()
}

/// The degradation kinds, in training/augment.py's order (ORDER).
const AUGMENT: [&str; 7] = ["room", "noise", "mic", "compress", "clip", "wow", "codec"];

/// The ticked degradation kinds, in augment.py's order; unknown ones are left out.
fn augment_kinds(s: &Settings) -> Vec<&'static str> {
    AUGMENT
        .into_iter()
        .filter(|k| s.augment.iter().any(|a| a == k))
        .collect()
}

/// The noise recordings folder, when `noise` is ticked and one is set.
fn augment_noise(s: &Settings) -> Option<PathBuf> {
    let dir = s.augment_noise.trim();
    (!dir.is_empty() && augment_kinds(s).contains(&"noise")).then(|| expand(dir))
}

/// train.augment (training/pipeline.py): no kinds ticked is off, and the run is the same as
/// before augmentation existed (same fingerprint, no copies).
fn augment_config(s: &Settings) -> serde_json::Value {
    serde_json::json!({
        "use": augment_kinds(s),
        "copies": s.augment_copies.clamp(1, 4),
        "noise_dir": augment_noise(s),
    })
}

/// The pipeline's config, from the settings; everything else keeps the pipeline's defaults
/// (training/pipeline.py DEFAULTS, the same as training/pipeline.example.json).
fn write_config(app: &AppHandle, s: &Settings, python: &str) -> Result<PathBuf, String> {
    let local = app.path().local_data_dir().map_err(|e| e.to_string())?;
    let surveil = apps::spec("surveil")
        .and_then(|spec| apps::find(spec, &local))
        .ok_or("BackTrack Surveil is not installed")?;
    let data = expand(&s.data);
    let npm = which(if cfg!(windows) { "npm.cmd" } else { "npm" }).or_else(|| which("npm"));
    let mut server = serde_json::json!({ "command": null, "cwd": null });
    if s.use_acestep && !s.acestep.trim().is_empty() {
        server = serde_json::json!({ "command": ["uv", "run", "acestep-api"], "cwd": expand(&s.acestep) });
    }
    // What each kind renders and learns (training/pipeline.py, KINDS); ACE-Step takes are judged
    // for the kind's part (Song Deck's dataset:perform --parts).
    let (name, stems, keep): (&str, &str, Vec<&str>) = match kind(s) {
        "bass" => ("Surveil bass", "bass", vec!["bass"]),
        "melody" => ("Surveil melody", "melody,vocals", vec!["vocals", "other"]),
        _ => ("Surveil drums", "drums", vec!["drums"]),
    };
    let cfg = serde_json::json!({
        "songdeck": expand(&s.songdeck),
        "backtrack_binary": surveil.binary,
        "python": python,
        "npm": npm,
        "dataset": dataset_dir(s),
        "runs_dir": runs_dir(s),
        "model": { "kind": kind(s), "name": name, "version": model_version(app, s)?, "file_stem": format!("surveil-{}", kind(s)) },
        "render": { "songs": s.songs.max(50), "only_stems": stems, "keep_separated": keep },
        "perform": { "enabled": s.use_acestep, "server": server },
        "train": {
            "epochs": s.epochs.max(1), "device": s.device, "audio": "stem,separated,performed",
            // Degraded copies of the training items (phone, live, tape, MP3); off by default.
            "augment": augment_config(s),
        },
        // Downloaded and imported once into the data folder by the pipeline's libraries step.
        "libraries": { "dir": data.join("libraries"), "use": s.libraries, "fetch": true },
    });
    std::fs::create_dir_all(&data).map_err(|e| format!("can't create {}: {e}", data.display()))?;
    let p = config_path(s);
    std::fs::write(&p, serde_json::to_string_pretty(&cfg).unwrap()).map_err(|e| e.to_string())?;
    Ok(p)
}

/// Starts a training run (or, with `resume`, picks the last one up where it stopped).
#[tauri::command]
pub async fn train_start(
    app: AppHandle,
    state: tauri::State<'_, Trainer>,
    settings: Settings,
    resume: bool,
) -> Result<(), String> {
    let settings = resolved(&settings);
    let dir = trainer_dir(&app)
        .filter(|d| d.join("training").is_dir())
        .ok_or("the training code is missing: reinstall BackTrack")?;
    let py = probe_python(&settings)?;
    if py.torch.is_none() {
        return Err(format!(
            "Python at {} can't load PyTorch: run CHECK SETUP",
            py.exe
        ));
    }
    let cfg = write_config(&app, &settings, &py.exe)?;
    let mut cmd = command(&py.exe);
    cmd.args(["-u", "-m", "training.pipeline", "--config"])
        .arg(&cfg)
        .current_dir(&dir)
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONDONTWRITEBYTECODE", "1") // the bundled code may sit in a read-only folder
        .env("BACKTRACK_HIDE_CONSOLES", "1");
    if resume {
        cmd.args(["--run", "latest"]);
    }
    spawn(&state, if resume { "resume" } else { "run" }, cmd)
}

const KINDS: [&str; 3] = ["drums", "bass", "melody"];

/// The queue's state (training/queue.py --status), in the data folder.
fn queue_status_path(s: &Settings) -> PathBuf {
    expand(&s.data).join("queue-status.json")
}

/// ONE AFTER ANOTHER: trains the ticked kinds in turn (training/queue.py), each with these
/// settings and its own songs and runs, optionally waiting for `start_at` first. With `resume`,
/// the stopped queue goes on: finished kinds are skipped, the stopped one resumes its run.
#[tauri::command]
pub async fn train_start_queue(
    app: AppHandle,
    state: tauri::State<'_, Trainer>,
    settings: Settings,
    resume: bool,
) -> Result<(), String> {
    let settings = resolved(&settings);
    let dir = trainer_dir(&app)
        .filter(|d| d.join("training").join("queue.py").is_file())
        .ok_or("the training code is missing or too old: reinstall BackTrack")?;
    let py = probe_python(&settings)?;
    if py.torch.is_none() {
        return Err(format!("Python at {} can't load PyTorch: run CHECK SETUP", py.exe));
    }
    let status = queue_status_path(&settings);
    let mut cmd = command(&py.exe);
    cmd.args(["-u", "-m", "training.queue", "--status"])
        .arg(&status)
        .current_dir(&dir)
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("BACKTRACK_HIDE_CONSOLES", "1");
    if resume {
        if !status.is_file() {
            return Err("there is no stopped queue to resume: start one".into());
        }
        cmd.arg("--resume");
    } else {
        let kinds: Vec<&str> = KINDS.into_iter().filter(|k| settings.queue.iter().any(|q| q == k)).collect();
        if kinds.is_empty() {
            return Err("tick at least one model to train".into());
        }
        for k in kinds {
            let s = Settings { kind: k.into(), ..settings.clone() };
            cmd.arg("--config").arg(write_config(&app, &s, &py.exe)?);
        }
    }
    let at = settings.start_at.trim();
    if !at.is_empty() {
        cmd.args(["--start-at", at]);
    }
    spawn(&state, "queue", cmd)
}

/// The queue's entries as (kind, status), and the kind running now (or the last one started).
fn queue_info(s: &Settings) -> Option<(Vec<(String, String)>, Option<String>)> {
    let st: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(queue_status_path(s)).ok()?).ok()?;
    let entries: Vec<(String, String)> = st["entries"]
        .as_array()?
        .iter()
        .map(|e| (e["kind"].as_str().unwrap_or("").to_string(), e["status"].as_str().unwrap_or("").to_string()))
        .collect();
    let current = st["current"]
        .as_u64()
        .and_then(|i| entries.get(i as usize))
        .or_else(|| entries.iter().rev().find(|(_, status)| status != "waiting"))
        .map(|(k, _)| k.clone());
    Some((entries, current))
}

/// uv's own installer (what docs.astral.sh/uv gives to copy), run for the user.
fn uv_install() -> Step {
    Box::new(|| {
        let mut c;
        if cfg!(windows) {
            c = command("powershell");
            c.args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "irm https://astral.sh/uv/install.ps1 | iex"]);
        } else {
            c = command("sh");
            c.args(["-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"]);
        }
        Ok(c)
    })
}

/// A Python 3.12 environment with PyTorch for the training, in the data folder's `venv` (the
/// setup check looks there). uv fetches Python 3.12 itself if the computer has none, and
/// PyTorch comes in the build that fits: CUDA with an NVIDIA GPU, the default on a Mac, else CPU.
fn python_install(s: &Settings) -> Vec<Step> {
    let data = if s.data.trim().is_empty() { home().join("backtrack-training") } else { expand(&s.data) };
    let venv = data.join("venv");
    let py = venv_python(&venv);
    let uv = || which("uv").ok_or("uv isn't installed: its installer didn't finish".to_string());
    let mut steps: Vec<Step> = Vec::new();
    {
        let venv = venv.clone();
        steps.push(Box::new(move || {
            if venv.join("pyvenv.cfg").is_file() {
                let _ = std::fs::remove_dir_all(&venv); // ours, from an earlier try
            }
            let mut c = command(uv()?);
            c.args(["venv", "--python", "3.12"]).arg(&venv);
            Ok(c)
        }));
    }
    {
        let py = py.clone();
        steps.push(Box::new(move || {
            let mut c = command(uv()?);
            c.args(["pip", "install", "--python"]).arg(&py).arg("numpy");
            Ok(c)
        }));
    }
    steps.push(Box::new(move || {
        let mut c = command(uv()?);
        c.args(["pip", "install", "--python"]).arg(&py).arg("torch");
        if !cfg!(target_os = "macos") {
            let gpu = which("nvidia-smi").is_some();
            c.args(["--index-url", if gpu { "https://download.pytorch.org/whl/cu128" } else { "https://download.pytorch.org/whl/cpu" }]);
        }
        Ok(c)
    }));
    steps
}

/// A setup fix: Song Deck's `npm install` or sound bank, uv, or Python with PyTorch.
#[tauri::command]
pub async fn train_fix(
    state: tauri::State<'_, Trainer>,
    settings: Settings,
    fix: String,
) -> Result<(), String> {
    let settings = resolved(&settings);
    match fix.as_str() {
        "uv-install" => return spawn_chain(&state, &fix, vec![uv_install()]),
        "python-install" => {
            let mut steps = Vec::new();
            if which("uv").is_none() {
                steps.push(uv_install());
            }
            steps.extend(python_install(&settings));
            return spawn_chain(&state, &fix, steps);
        }
        _ => {}
    }
    let npm = which(if cfg!(windows) { "npm.cmd" } else { "npm" })
        .or_else(|| which("npm"))
        .ok_or("npm not found: install Node.js")?;
    let mut cmd = command(npm);
    cmd.current_dir(expand(&settings.songdeck));
    match fix.as_str() {
        "songdeck-install" => cmd.arg("install"),
        "soundbank" => cmd.args(["run", "soundbank:build"]),
        _ => return Err(format!("unknown fix {fix}")),
    };
    spawn(&state, &fix, cmd)
}

/// STOP: the pipeline marks the run interrupted and RESUME continues it. Async, so the wait
/// for it to stop never holds up the window.
#[tauri::command]
pub async fn train_stop(state: tauri::State<'_, Trainer>) -> Result<(), ()> {
    stop(&state);
    Ok(())
}

/// Whether the job's process has ended (taking the lock only briefly, so polling goes on).
fn ended(state: &Trainer) -> bool {
    let mut job = state.job.lock().unwrap();
    match job.child.as_mut() {
        Some(c) => c.try_wait().ok().flatten().is_some(),
        None => true,
    }
}

pub fn stop(state: &Trainer) {
    let pid = {
        let mut job = state.job.lock().unwrap();
        job.queue.clear();
        let Some(c) = job.child.as_mut() else { return };
        if c.try_wait().ok().flatten().is_some() {
            return;
        }
        c.id()
    };
    #[cfg(unix)]
    {
        // The job runs in its own process group (see spawn): SIGTERM to the group, so the
        // pipeline records where it stopped and what it started stops too; SIGKILL if it lingers.
        let Ok(pgid) = i32::try_from(pid) else { return };
        if pgid <= 1 {
            return;
        }
        unsafe { libc::kill(-pgid, libc::SIGTERM) };
        for _ in 0..50 {
            if ended(state) {
                return;
            }
            std::thread::sleep(Duration::from_millis(200));
        }
        unsafe { libc::kill(-pgid, libc::SIGKILL) };
    }
    #[cfg(windows)]
    {
        let _ = command("taskkill")
            .args(["/T", "/F", "/PID", &pid.to_string()])
            .status();
    }
    for _ in 0..50 {
        if ended(state) {
            return;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

// ---------------------------------------------------------------------------- progress

#[derive(Serialize)]
pub struct Progress {
    running: bool,
    kind: String,
    exit: Option<i32>,
    lines: Vec<String>,
    next: usize,
    run: Option<String>,
    steps: Vec<(String, String)>, // (step, status)
    report: Option<String>,       // report.md of the shown run, once written
    summary: Option<serde_json::Value>,
    best: Option<serde_json::Value>, // the best model so far, its Song Deck plugin and whether it's installed
    queue: Option<Vec<(String, String)>>, // during a queue: (kind, status) of each entry
    shown: String,                        // the kind whose run is shown (a queue's current one)
}

/// Song Deck's plugins folder for plugins you add: <data dir>/plugins, the data dir being
/// $SONGDECK_DATA_DIR or ~/.songdeck (Song Deck's server and desktop app both use it).
fn songdeck_plugins_dir() -> PathBuf {
    std::env::var_os("SONGDECK_DATA_DIR")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| home().join(".songdeck"))
        .join("plugins")
}

/// runs/best's Song Deck plugin folder (the pipeline's report step writes it): (id, folder).
fn best_plugin(s: &Settings) -> Option<(String, PathBuf)> {
    let best = runs_dir(s).join("best");
    let info: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(best.join("best.json")).ok()?).ok()?;
    let id = info["plugin"].as_str()?.to_string();
    let dir = best.join("songdeck-plugin").join(&id);
    dir.join("songdeck-plugin.json").is_file().then_some((id, dir))
}

fn best_info(s: &Settings) -> Option<serde_json::Value> {
    let best = runs_dir(s).join("best");
    let info: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(best.join("best.json")).ok()?).ok()?;
    let report: serde_json::Value = std::fs::read_to_string(best.join("report.json"))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_default();
    let plugin = best_plugin(s);
    let installed = plugin.as_ref().is_some_and(|(id, dir)| {
        let there = songdeck_plugins_dir().join(id).join("songdeck-plugin.json");
        std::fs::read(there).ok() == std::fs::read(dir.join("songdeck-plugin.json")).ok()
    });
    Some(serde_json::json!({
        "run": info["run"],
        "value": info["value"],
        "songdeck": report["promotion"]["songdeck_internal"],
        "plugin": plugin.map(|(id, _)| id),
        "installed": installed,
        "plugins_dir": songdeck_plugins_dir(),
    }))
}

/// INSTALL IN SONG DECK: copies runs/best's model plugin into Song Deck's plugins folder,
/// replacing an earlier one with the same id. Song Deck then lists it under Settings → Plugins.
#[tauri::command]
pub async fn train_install(settings: Settings) -> Result<String, String> {
    let settings = resolved(&settings);
    let (id, src) = best_plugin(&settings).ok_or("no trained model to install yet: finish a training run first")?;
    let dest = songdeck_plugins_dir().join(&id);
    let tmp = songdeck_plugins_dir().join(format!(".{id}.installing"));
    let _ = std::fs::remove_dir_all(&tmp);
    std::fs::create_dir_all(&tmp).map_err(|e| format!("can't write to {}: {e}", tmp.display()))?;
    for entry in std::fs::read_dir(&src).map_err(|e| e.to_string())? {
        let p = entry.map_err(|e| e.to_string())?.path();
        if p.is_file() {
            std::fs::copy(&p, tmp.join(p.file_name().unwrap())).map_err(|e| format!("copying {}: {e}", p.display()))?;
        }
    }
    let _ = std::fs::remove_dir_all(&dest);
    std::fs::rename(&tmp, &dest).map_err(|e| format!("can't install into {}: {e}", dest.display()))?;
    Ok(dest.to_string_lossy().into_owned())
}

/// The newest run folder in the runs directory (names are timestamps).
fn latest_run(s: &Settings) -> Option<PathBuf> {
    let runs = runs_dir(s);
    let mut dirs: Vec<PathBuf> = std::fs::read_dir(runs)
        .ok()?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| {
            p.is_dir()
                && p.file_name()
                    .and_then(|n| n.to_str())
                    .is_some_and(|n| n.starts_with(|c: char| c.is_ascii_digit()))
        })
        .collect();
    dirs.sort();
    dirs.pop()
}

#[tauri::command]
pub async fn train_poll(
    state: tauri::State<'_, Trainer>,
    settings: Settings,
    since: usize,
) -> Result<Progress, ()> {
    let (running, kind, exit, lines, next) = {
        let mut job = state.job.lock().unwrap();
        let ended = job.child.as_mut().and_then(|c| c.try_wait().ok().flatten());
        if let Some(st) = ended {
            job.child = None;
            job.exit = Some(st.code().unwrap_or(-1));
            if st.success() && !job.queue.is_empty() {
                job.exit = None;
                if let Err(e) = next_step(&mut job, &state.job) {
                    job.lines.push(e);
                    job.queue.clear();
                    job.exit = Some(1);
                }
            } else {
                job.queue.clear();
            }
        }
        let start = since.saturating_sub(job.dropped).min(job.lines.len());
        (
            job.child.is_some(),
            job.kind.clone(),
            job.exit,
            job.lines[start..].to_vec(),
            job.dropped + job.lines.len(),
        )
    };
    // During a queue (and after it) the run shown is the queue's current kind's.
    let mut queue = None;
    let mut settings = settings;
    if kind == "queue" {
        if let Some((entries, current)) = queue_info(&settings) {
            if let Some(k) = current {
                settings.kind = k;
            }
            queue = Some(entries);
        }
    }
    let run_dir = latest_run(&settings);
    let mut steps = Vec::new();
    let mut report = None;
    let mut summary = None;
    if let Some(dir) = &run_dir {
        let st: serde_json::Value = std::fs::read_to_string(dir.join("state.json"))
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
            .unwrap_or_default();
        for name in STEPS {
            let status = st["steps"][name]["status"]
                .as_str()
                .unwrap_or("waiting")
                .to_string();
            steps.push((name.to_string(), status));
        }
        report = std::fs::read_to_string(dir.join("report.md")).ok();
        summary = std::fs::read_to_string(dir.join("report.json"))
            .ok()
            .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
            .map(|r| serde_json::json!({ "promotion": r["promotion"] }));
    }
    Ok(Progress {
        running,
        kind,
        exit,
        lines,
        next,
        run: run_dir.and_then(|d| d.file_name().map(|n| n.to_string_lossy().into_owned())),
        steps,
        report,
        summary,
        best: best_info(&settings),
        queue,
        shown: crate::train::kind(&settings).to_string(),
    })
}

#[cfg(test)]
mod tests {
    use super::{next_version, parse_version};

    #[test]
    fn model_versions_are_plain_semver() {
        assert_eq!(parse_version("0.2.0"), Some((0, 2, 0)));
        assert_eq!(parse_version(" 1.10.3 "), Some((1, 10, 3)));
        for bad in ["", "1.2", "1.2.3.4", "1.2.x", "v1.2.3", "1..3", "1.2.3-rc1", "-1.2.3"] {
            assert_eq!(parse_version(bad), None, "{bad:?}");
        }
    }

    #[test]
    fn the_suggestion_never_reuses_a_released_version() {
        assert_eq!(next_version(&[]), "0.1.0");
        assert_eq!(next_version(&[(0, 1, 0)]), "0.2.0");
        assert_eq!(next_version(&[(0, 1, 0), (0, 2, 0)]), "0.3.0");
        assert_eq!(next_version(&[(1, 4, 2)]), "1.5.0");
    }
}
