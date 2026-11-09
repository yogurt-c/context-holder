//! 엔진(`ctx serve --app`)을 띄우고 감시한다. 엔진과는 stdin · stdout 줄로 주고받는다.
//!
//! - stdout: 준비 줄 `{"port","token","url"}`, 그 뒤 `{"event": "status" | "notify", ...}`
//! - stdin: 메뉴 명령 한 줄 (`sync_now`, `pause 1h`, `resume`)
//! - 엔진이 비정상 종료하면 다시 띄운다 (최대 MAX_RESTARTS번, 점점 늦게).
//! - 앱이 끝나면 엔진을 끈다. 앱이 강제로 죽으면 엔진은 stdin EOF를 보고 스스로 끝난다.

use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use serde::Deserialize;
use serde_json::Value;

const MAX_RESTARTS: u32 = 3;

/// 메뉴 항목 하나. 엔진이 정한 대로 그린다.
#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct Item {
    #[serde(default)]
    pub label: String,
    #[serde(default = "yes")]
    pub enabled: bool,
    #[serde(default)]
    pub separator: bool,
    pub open: Option<String>,
    pub cmd: Option<String>,
    #[serde(default)]
    pub quit: bool,
    #[serde(default)]
    pub children: Vec<Item>,
}

fn yes() -> bool {
    true
}

#[derive(Debug, Clone, PartialEq)]
pub enum Line {
    Ready {
        url: String,
        base: String,
    },
    Status {
        icon: String,
        items: Vec<Item>,
    },
    Notify {
        title: String,
        body: String,
        open: String,
    },
}

/// 엔진 stdout 한 줄을 읽는다. 모르는 줄은 None.
pub fn parse(line: &str) -> Option<Line> {
    let value: Value = serde_json::from_str(line.trim()).ok()?;
    match value.get("event").and_then(Value::as_str) {
        None => {
            let port = value.get("port")?.as_u64()?;
            let url = value.get("url")?.as_str()?.to_string();
            Some(Line::Ready {
                url,
                base: format!("http://127.0.0.1:{port}"),
            })
        }
        Some("status") => Some(Line::Status {
            icon: value.get("icon")?.as_str()?.to_string(),
            items: serde_json::from_value(value.get("items")?.clone()).ok()?,
        }),
        Some("notify") => Some(Line::Notify {
            title: value.get("title")?.as_str()?.to_string(),
            body: value
                .get("body")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
            open: value
                .get("open")
                .and_then(Value::as_str)
                .unwrap_or("/today")
                .to_string(),
        }),
        Some(_) => None,
    }
}

#[derive(Clone)]
pub struct Engine {
    exe: PathBuf,
    child: Arc<Mutex<Option<Child>>>,
    stdin: Arc<Mutex<Option<ChildStdin>>>,
    stopping: Arc<AtomicBool>,
}

impl Engine {
    pub fn new(exe: PathBuf) -> Self {
        Engine {
            exe,
            child: Arc::new(Mutex::new(None)),
            stdin: Arc::new(Mutex::new(None)),
            stopping: Arc::new(AtomicBool::new(false)),
        }
    }

    /// 엔진을 띄우고 줄마다 on_line을 부른다. 죽으면 다시 띄운다. 이 함수는 돌아오지 않는다.
    pub fn run(&self, on_line: impl Fn(Line) + Send + 'static) {
        let mut restarts = 0;
        loop {
            let spawned = Command::new(&self.exe)
                .args(["serve", "--app"])
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::inherit())
                .spawn();
            let mut child = match spawned {
                Ok(child) => child,
                Err(e) => {
                    eprintln!("엔진을 띄우지 못했다 {}: {e}", self.exe.display());
                    return;
                }
            };
            let stdout = child.stdout.take().expect("stdout");
            *self.stdin.lock().unwrap() = child.stdin.take();
            *self.child.lock().unwrap() = Some(child);
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if let Some(parsed) = parse(&line) {
                    on_line(parsed);
                }
            }
            let code = self
                .child
                .lock()
                .unwrap()
                .as_mut()
                .and_then(|c| c.wait().ok());
            if self.stopping.load(Ordering::SeqCst) {
                return;
            }
            eprintln!("엔진이 끝났다 ({code:?})");
            if restarts >= MAX_RESTARTS {
                eprintln!("엔진을 다시 띄우지 않는다");
                return;
            }
            restarts += 1;
            thread::sleep(Duration::from_millis(500 * u64::from(restarts)));
        }
    }

    /// 메뉴 명령을 엔진에 보낸다.
    pub fn send(&self, command: &str) {
        if let Some(stdin) = self.stdin.lock().unwrap().as_mut() {
            let _ = writeln!(stdin, "{command}");
            let _ = stdin.flush();
        }
    }

    pub fn stop(&self) {
        self.stopping.store(true, Ordering::SeqCst);
        self.stdin.lock().unwrap().take(); // stdin을 닫으면 엔진이 스스로 끝난다
        if let Some(child) = self.child.lock().unwrap().as_mut() {
            for _ in 0..20 {
                if let Ok(Some(_)) = child.try_wait() {
                    return;
                }
                thread::sleep(Duration::from_millis(50));
            }
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ready_line() {
        let line = r#"{"port": 51234, "token": "t", "url": "http://127.0.0.1:51234/?token=t"}"#;
        assert_eq!(
            parse(line),
            Some(Line::Ready {
                url: "http://127.0.0.1:51234/?token=t".into(),
                base: "http://127.0.0.1:51234".into()
            })
        );
    }

    #[test]
    fn status_line_with_submenu() {
        let line = r#"{"event": "status", "icon": "busy", "items": [
            {"label": "수집 중", "enabled": false},
            {"separator": true},
            {"label": "검색", "enabled": true, "open": "/search"},
            {"label": "일시정지", "enabled": true, "children": [
                {"label": "1시간", "enabled": true, "cmd": "pause 1h"}]},
            {"label": "종료", "enabled": true, "quit": true}]}"#;
        let Some(Line::Status { icon, items }) = parse(line) else {
            panic!("status")
        };
        assert_eq!(icon, "busy");
        assert!(!items[0].enabled && items[1].separator);
        assert_eq!(items[2].open.as_deref(), Some("/search"));
        assert_eq!(items[3].children[0].cmd.as_deref(), Some("pause 1h"));
        assert!(items[4].quit);
    }

    #[test]
    fn notify_line_defaults() {
        let line = r#"{"event": "notify", "title": "오늘의 브리프"}"#;
        assert_eq!(
            parse(line),
            Some(Line::Notify {
                title: "오늘의 브리프".into(),
                body: "".into(),
                open: "/today".into()
            })
        );
    }

    #[test]
    fn unknown_or_broken_lines() {
        assert_eq!(parse("저장 1 · 보류 0"), None);
        assert_eq!(parse(r#"{"event": "progress"}"#), None);
        assert_eq!(parse(r#"{"event": "status"}"#), None);
        assert_eq!(parse(r#"{"token": "t"}"#), None);
    }
}
