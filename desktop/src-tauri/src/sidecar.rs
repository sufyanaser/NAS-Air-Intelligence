//! Lifecycle of the hidden Python sidecar: spawn, ready handshake, health, shutdown.
//!
//! Contract with `nas_air_intelligence.sidecar`:
//! * the token is passed in `NAS_AIR_SIDECAR_TOKEN`;
//! * the sidecar prints one JSON line `{"event":"ready","port":N,"pid":P}` on stdout;
//! * every request carries `Authorization: Bearer <token>`;
//! * the sidecar exits when its stdin closes, so holding `stdin` open ties its life to ours.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant};

use serde::Deserialize;

const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const READY_TIMEOUT: Duration = Duration::from_secs(60);
const HTTP_TIMEOUT: Duration = Duration::from_secs(3);

#[derive(Debug, Deserialize, PartialEq, Eq)]
pub struct ReadyLine {
    pub event: String,
    pub port: u16,
    pub pid: u32,
}

pub fn parse_ready_line(line: &str) -> Result<ReadyLine, String> {
    let ready: ReadyLine =
        serde_json::from_str(line.trim()).map_err(|e| format!("bad ready line {line:?}: {e}"))?;
    if ready.event != "ready" {
        return Err(format!("unexpected sidecar event: {}", ready.event));
    }
    Ok(ready)
}

pub fn new_token() -> Result<String, String> {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).map_err(|e| format!("no random source: {e}"))?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

/// A request as the sidecar sees it; the response body is returned only for HTTP 200.
pub fn http_request(
    port: u16,
    method: &str,
    path: &str,
    token: &str,
) -> Result<serde_json::Value, String> {
    let addr = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
    let mut stream =
        TcpStream::connect_timeout(&addr, HTTP_TIMEOUT).map_err(|e| format!("connect: {e}"))?;
    stream
        .set_read_timeout(Some(HTTP_TIMEOUT))
        .map_err(|e| e.to_string())?;
    stream
        .set_write_timeout(Some(HTTP_TIMEOUT))
        .map_err(|e| e.to_string())?;
    let request = format!(
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {token}\r\n\
         Content-Length: 0\r\nConnection: close\r\n\r\n"
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|e| format!("write: {e}"))?;
    let mut raw = Vec::new();
    stream
        .read_to_end(&mut raw)
        .map_err(|e| format!("read: {e}"))?;
    parse_http_response(&raw)
}

pub fn parse_http_response(raw: &[u8]) -> Result<serde_json::Value, String> {
    let text = String::from_utf8_lossy(raw);
    let (head, body) = text
        .split_once("\r\n\r\n")
        .ok_or("malformed HTTP response")?;
    let status: u16 = head
        .lines()
        .next()
        .and_then(|l| l.split_whitespace().nth(1))
        .and_then(|s| s.parse().ok())
        .ok_or("malformed HTTP status line")?;
    if status != 200 {
        return Err(format!("sidecar returned HTTP {status}"));
    }
    serde_json::from_str(body.trim()).map_err(|e| format!("bad JSON body: {e}"))
}

pub struct Sidecar {
    child: Child,
    // Held open on purpose: dropping it closes the sidecar's stdin and makes it exit.
    _stdin: Option<ChildStdin>,
    pub port: u16,
    pub token: String,
}

impl Sidecar {
    /// Ask nicely, wait briefly, then kill. The job object is the final backstop.
    pub fn shutdown(&mut self) {
        let _ = http_request(self.port, "POST", "/shutdown", &self.token);
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if matches!(self.child.try_wait(), Ok(Some(_))) {
                return;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

enum Launch {
    Exe(PathBuf),
    /// Development only: run the Python core straight from the repository venv.
    #[cfg_attr(not(debug_assertions), allow(dead_code))]
    PythonModule {
        python: PathBuf,
        repo_src: PathBuf,
    },
}

fn resolve_launch() -> Result<Launch, String> {
    if let Ok(path) = std::env::var("NAS_AIR_SIDECAR_EXE") {
        return Ok(Launch::Exe(PathBuf::from(path)));
    }
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    if let Some(dir) = exe.parent() {
        let bundled = dir.join("nas-air-sidecar.exe");
        if bundled.exists() {
            return Ok(Launch::Exe(bundled));
        }
    }
    #[cfg(debug_assertions)]
    {
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..");
        let python = repo.join(".venv").join("Scripts").join("python.exe");
        if python.exists() {
            return Ok(Launch::PythonModule {
                python,
                repo_src: repo.join("src"),
            });
        }
    }
    Err("sidecar executable not found next to the app".into())
}

pub fn start() -> Result<Sidecar, String> {
    let token = new_token()?;
    let mut command = match resolve_launch()? {
        Launch::Exe(path) => Command::new(path),
        Launch::PythonModule { python, repo_src } => {
            let mut c = Command::new(python);
            c.args(["-m", "nas_air_intelligence.sidecar"])
                .env("PYTHONPATH", repo_src);
            c
        }
    };
    command
        .env("NAS_AIR_SIDECAR_TOKEN", &token)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = command
        .spawn()
        .map_err(|e| format!("cannot start sidecar: {e}"))?;
    let stdin = child.stdin.take();
    let stdout = child.stdout.take().ok_or("sidecar stdout unavailable")?;

    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut line = String::new();
        let first = reader.read_line(&mut line).map(|_| line.clone());
        let _ = tx.send(first);
        // Keep draining so the sidecar can never block on a full stdout pipe.
        let mut sink = String::new();
        while reader.read_line(&mut sink).map(|n| n > 0).unwrap_or(false) {
            sink.clear();
        }
    });

    let ready = match rx.recv_timeout(READY_TIMEOUT) {
        Ok(Ok(line)) if !line.trim().is_empty() => parse_ready_line(&line),
        Ok(Ok(_)) => Err("sidecar exited before it was ready".to_string()),
        Ok(Err(e)) => Err(format!("cannot read sidecar output: {e}")),
        Err(_) => Err("sidecar did not become ready in time".to_string()),
    };
    match ready {
        Ok(ready) => Ok(Sidecar {
            child,
            _stdin: stdin,
            port: ready.port,
            token,
        }),
        Err(e) => {
            let _ = child.kill();
            let _ = child.wait();
            Err(e)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_ready_line() {
        let r = parse_ready_line("{\"event\":\"ready\",\"port\":5123,\"pid\":42}\r\n").unwrap();
        assert_eq!(
            r,
            ReadyLine {
                event: "ready".into(),
                port: 5123,
                pid: 42
            }
        );
    }

    #[test]
    fn rejects_bad_ready_lines() {
        assert!(parse_ready_line("hello").is_err());
        assert!(parse_ready_line("{\"event\":\"boot\",\"port\":1,\"pid\":2}").is_err());
    }

    #[test]
    fn token_is_64_hex_chars_and_unique() {
        let (a, b) = (new_token().unwrap(), new_token().unwrap());
        assert_eq!(a.len(), 64);
        assert!(a.chars().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(a, b);
    }

    #[test]
    fn parses_http_responses() {
        let ok = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"status\":\"ready\"}";
        assert_eq!(parse_http_response(ok).unwrap()["status"], "ready");
        let denied = b"HTTP/1.1 401 Unauthorized\r\n\r\n{\"error\":\"unauthorized\"}";
        assert!(parse_http_response(denied).unwrap_err().contains("401"));
        assert!(parse_http_response(b"garbage").is_err());
    }
}
