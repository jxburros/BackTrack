// Runs an app's server (`<binary> serve`) in the background and waits until it answers.
//
// The server goes on the app's usual port when that is free, so Song Deck finds it there too;
// if the app is already serving there (Song Deck started it, or another window did), that server
// is used as it is. Otherwise any free port will do.

use crate::apps::{AppSpec, Install};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub struct Running {
    pub url: String,
    pub child: Option<Child>, // None when an already-running server is used
}

/// GET /health on 127.0.0.1:port; the parsed body when it is one of ours.
pub fn health(port: u16) -> Option<serde_json::Value> {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let mut s = TcpStream::connect_timeout(&addr, Duration::from_millis(300)).ok()?;
    s.set_read_timeout(Some(Duration::from_secs(2))).ok()?;
    s.set_write_timeout(Some(Duration::from_secs(2))).ok()?;
    write!(
        s,
        "GET /health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    )
    .ok()?;
    let mut buf = Vec::new();
    s.read_to_end(&mut buf).ok()?;
    let text = String::from_utf8_lossy(&buf);
    let (head, body) = text.split_once("\r\n\r\n")?;
    if !head.starts_with("HTTP/1.1 200") && !head.starts_with("HTTP/1.0 200") {
        return None;
    }
    serde_json::from_str(body.trim()).ok()
}

fn serving(spec: &AppSpec, port: u16) -> bool {
    health(port).is_some_and(|h| h.get("app").and_then(|a| a.as_str()) == Some(spec.id))
}

fn port_free(port: u16) -> bool {
    TcpListener::bind(("127.0.0.1", port)).is_ok()
}

fn any_free_port() -> Result<u16, String> {
    let l = TcpListener::bind(("127.0.0.1", 0)).map_err(|e| format!("no free port: {e}"))?;
    Ok(l.local_addr().map_err(|e| e.to_string())?.port())
}

fn url(port: u16) -> String {
    format!("http://127.0.0.1:{port}/")
}

pub fn start(
    spec: &AppSpec,
    install: &Install,
    extra_models: &[PathBuf],
) -> Result<Running, String> {
    if serving(spec, spec.port) {
        return Ok(Running {
            url: url(spec.port),
            child: None,
        });
    }
    if !install.binary.is_file() {
        return Err(format!(
            "{} is missing; reinstall {}.",
            install.binary.display(),
            spec.name
        ));
    }
    let port = if port_free(spec.port) {
        spec.port
    } else {
        any_free_port()?
    };

    let mut cmd = Command::new(&install.binary);
    cmd.arg("serve")
        .arg("--port")
        .arg(port.to_string())
        .arg("--quiet")
        .arg("--watch-stdin");
    for dir in install.models.iter().chain(extra_models) {
        if dir.is_dir() {
            cmd.arg("--models-dir").arg(dir);
        }
    }
    // stdin stays open as a pipe for as long as we run: --watch-stdin stops the server when it closes.
    cmd.stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("Couldn't run {}: {e}.", install.binary.display()))?;

    // Keep the last of what it says, for the error if it stops.
    let said = Arc::new(Mutex::new(String::new()));
    if let Some(mut err) = child.stderr.take() {
        let said = said.clone();
        std::thread::spawn(move || {
            let mut chunk = [0u8; 1024];
            while let Ok(n) = err.read(&mut chunk) {
                if n == 0 {
                    break;
                }
                let mut s = said.lock().unwrap();
                s.push_str(&String::from_utf8_lossy(&chunk[..n]));
                if s.len() > 4000 {
                    let cut = s.len() - 4000;
                    let cut = (cut..s.len())
                        .find(|&i| s.is_char_boundary(i))
                        .unwrap_or(cut);
                    s.drain(..cut);
                }
            }
        });
    }

    let deadline = Instant::now() + Duration::from_secs(60);
    loop {
        if let Ok(Some(status)) = child.try_wait() {
            std::thread::sleep(Duration::from_millis(100)); // let the reader catch the last words
            let text = said.lock().unwrap().trim().to_string();
            let why = text
                .strip_prefix("error: ")
                .unwrap_or(&text)
                .trim_end_matches('.')
                .to_string();
            let why = if why.is_empty() {
                format!("it exited ({status})")
            } else {
                why
            };
            return Err(format!("It stopped while starting: {why}."));
        }
        if serving(spec, port) {
            return Ok(Running {
                url: url(port),
                child: Some(child),
            });
        }
        if Instant::now() > deadline {
            let _ = child.kill();
            return Err(format!("It didn't answer on port {port} within a minute."));
        }
        std::thread::sleep(Duration::from_millis(150));
    }
}
