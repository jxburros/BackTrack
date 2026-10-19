// The hub's TRAIN view: runs the training pipeline (training/pipeline.py, bundled with the hub as
// a resource) for the drum model, with its settings, a setup check and the run's progress.
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
const STEPS: [&str; 6] = ["render", "perform", "train", "evaluate", "parity", "report"];

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
}

impl Default for Settings {
    fn default() -> Self {
        let home = home();
        let guess = |names: &[&str]| {
            for base in [
                home.clone(),
                home.join("Documents"),
                home.join("src"),
                home.join("code"),
            ] {
                for n in names {
                    let p = base.join(n);
                    if p.is_dir() {
                        return p.to_string_lossy().into_owned();
                    }
                }
            }
            String::new()
        };
        Settings {
            songdeck: guess(&["Song-Deck", "song-deck", "SongDeck"]),
            acestep: guess(&["ACE-Step-1.5", "ace-step-1.5", "ACE-Step"]),
            use_acestep: true,
            python: String::new(),
            data: home
                .join("backtrack-training")
                .to_string_lossy()
                .into_owned(),
            songs: 400,
            epochs: 30,
            device: "auto".into(),
        }
    }
}

#[derive(Default)]
struct Job {
    child: Option<Child>,
    kind: String, // run | resume | songdeck-install | soundbank
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

/// A desktop app on macOS or Linux starts with a bare PATH, without Homebrew, ~/.local/bin and
/// the like; the login shell's PATH has what a terminal would find (node, npm, uv, python).
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

// ---------------------------------------------------------------------------- setup check

#[derive(Serialize)]
pub struct Check {
    name: &'static str,
    state: &'static str, // ok | warn | fail
    detail: String,
    fix: Option<&'static str>, // a job the hub can run: songdeck-install | soundbank
}

#[derive(Serialize, Deserialize, Default)]
struct PythonInfo {
    exe: String,
    version: String,
    torch: Option<String>,
    cuda: bool,
    mps: bool,
    error: Option<String>,
}

const PROBE: &str = "import json,sys\nr={'exe':sys.executable,'version':sys.version.split()[0]}\ntry:\n import numpy,torch\n r['torch']=torch.__version__\n r['cuda']=bool(torch.cuda.is_available())\n m=getattr(torch.backends,'mps',None)\n r['mps']=bool(m and m.is_available())\nexcept Exception as e:\n r['error']=str(e)\nprint(json.dumps(r))";

fn probe_python(s: &Settings) -> Result<PythonInfo, String> {
    let mut tried = Vec::new();
    let cands: Vec<String> = if !s.python.trim().is_empty() {
        vec![s.python.trim().to_string()]
    } else if cfg!(windows) {
        vec!["py".into(), "python".into(), "python3".into()]
    } else {
        vec!["python3".into(), "python".into()]
    };
    let mut best: Option<PythonInfo> = None;
    for c in cands {
        let prog = if c.contains('/') || c.contains('\\') {
            Some(expand(&c))
        } else {
            which(&c)
        };
        let Some(prog) = prog else {
            tried.push(c);
            continue;
        };
        let out = command(&prog).args(["-c", PROBE]).output();
        if let Ok(out) = out {
            if let Ok(info) = serde_json::from_slice::<PythonInfo>(&out.stdout) {
                if info.torch.is_some() {
                    return Ok(info);
                }
                best.get_or_insert(info);
                continue;
            }
        }
        tried.push(c);
    }
    best.ok_or_else(|| format!("no Python found (tried {})", tried.join(", ")))
}

fn acestep_up() -> bool {
    std::net::TcpStream::connect_timeout(&([127, 0, 0, 1], 8001).into(), Duration::from_millis(300))
        .is_ok()
}

#[tauri::command]
pub async fn train_check(app: AppHandle, settings: Settings) -> Vec<Check> {
    let s = settings;
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
            push("Python and PyTorch", state, format!("Python {} with PyTorch {}, {gpu}: {}", p.version, p.torch.unwrap_or_default(), p.exe), None);
        }
        Ok(p) => push("Python and PyTorch", "fail", format!(
            "Python {} at {} can't load PyTorch ({}). Install it: see Python and PyTorch in docs/training-at-home.md, or choose another Python above.",
            p.version, p.exe, p.error.unwrap_or_default()), None),
        Err(e) => push("Python and PyTorch", "fail",
            format!("{e}. Install Python 3.11 or 3.12 and PyTorch, or choose your Python above."), None),
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
            "choose your Song Deck folder (the one with package.json) above.".into(),
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
        push("Song Deck", "ok", sd.display().to_string(), None);
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
            push("ACE-Step", "fail", "choose your ACE-Step 1.5 folder above (BackTrack starts it for each run), start it yourself, or untick Use ACE-Step.".into(), None);
        } else if which("uv").is_none() {
            push("ACE-Step", "fail", "uv not found, which starts ACE-Step: install uv (docs.astral.sh/uv), then check again.".into(), None);
        } else {
            push(
                "ACE-Step",
                "ok",
                format!(
                    "BackTrack starts it in {} for each run and stops it before training",
                    expand(&s.acestep).display()
                ),
                None,
            );
        }
    }
    out
}

// ---------------------------------------------------------------------------- jobs

fn spawn(state: &Trainer, kind: &str, mut cmd: Command) -> Result<(), String> {
    let mut job = state.job.lock().unwrap();
    if let Some(c) = job.child.as_mut() {
        if c.try_wait().ok().flatten().is_none() {
            return Err("something is already running: stop it first".into());
        }
    }
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0); // STOP reaches everything it started
    }
    let mut child = cmd.spawn().map_err(|e| format!("couldn't start: {e}"))?;
    *job = Job {
        kind: kind.into(),
        ..Job::default()
    };
    let shared = state.job.clone();
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

fn config_path(s: &Settings) -> PathBuf {
    expand(&s.data).join("pipeline.json")
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
    let cfg = serde_json::json!({
        "songdeck": expand(&s.songdeck),
        "backtrack_binary": surveil.binary,
        "python": python,
        "npm": npm,
        "dataset": data.join("sd-drums"),
        "runs_dir": data.join("runs"),
        "render": { "songs": s.songs.max(50) },
        "perform": { "enabled": s.use_acestep, "server": server },
        "train": { "epochs": s.epochs.max(1), "device": s.device },
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

/// A setup fix: Song Deck's `npm install`, or its sound bank.
#[tauri::command]
pub async fn train_fix(
    state: tauri::State<'_, Trainer>,
    settings: Settings,
    fix: String,
) -> Result<(), String> {
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
}

/// The newest run folder in the runs directory (names are timestamps).
fn latest_run(s: &Settings) -> Option<PathBuf> {
    let runs = expand(&s.data).join("runs");
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
        if let Some(c) = job.child.as_mut() {
            if let Ok(Some(st)) = c.try_wait() {
                job.exit = Some(st.code().unwrap_or(-1));
                job.child = None;
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
    })
}
