//! sidecar 스파이크: resources에 넣은 onedir 엔진을 띄우고 감시한다.
//!
//! - 엔진 stdout을 한 줄씩 로그에 남긴다.
//! - 엔진이 비정상 종료하면 다시 띄운다 (최대 MAX_RESTARTS번, 점점 늦게).
//! - 앱이 끝나면 엔진을 끈다. 앱이 강제로 죽으면 엔진은 stdin EOF를 보고 스스로 끝난다.
//! - SPIKE_LOG: 로그 파일, SPIKE_EXIT_AFTER: 이 초 뒤에 앱을 스스로 끝낸다 (자동 확인용).

use std::fs::OpenOptions;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent};

const MAX_RESTARTS: u32 = 3;

#[derive(Clone)]
struct Supervisor {
    child: Arc<Mutex<Option<Child>>>,
    stopping: Arc<AtomicBool>,
    log: PathBuf,
}

impl Supervisor {
    fn log(&self, line: &str) {
        if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&self.log) {
            let _ = writeln!(f, "{line}");
        }
    }

    fn run(&self, exe: PathBuf) {
        let mut restarts = 0;
        loop {
            let started = Instant::now();
            let mut child = match Command::new(&exe)
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::inherit())
                .spawn()
            {
                Ok(c) => c,
                Err(e) => {
                    self.log(&format!("app: spawn failed {e}"));
                    return;
                }
            };
            self.log(&format!("app: spawned pid={}", child.id()));
            let stdout = child.stdout.take().expect("stdout");
            *self.child.lock().unwrap() = Some(child);

            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                self.log(&format!("engine: {line}"));
            }
            let status = self
                .child
                .lock()
                .unwrap()
                .as_mut()
                .and_then(|c| c.wait().ok());
            self.log(&format!(
                "app: engine exited {:?} after {:.1}s",
                status.and_then(|s| s.code()),
                started.elapsed().as_secs_f32()
            ));
            if self.stopping.load(Ordering::SeqCst) {
                return;
            }
            if restarts >= MAX_RESTARTS {
                self.log("app: giving up");
                return;
            }
            restarts += 1;
            thread::sleep(Duration::from_millis(500 * u64::from(restarts)));
            self.log(&format!("app: restart #{restarts}"));
        }
    }

    fn stop(&self) {
        self.stopping.store(true, Ordering::SeqCst);
        if let Some(child) = self.child.lock().unwrap().as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
        self.log("app: stopped engine");
    }
}

fn engine_path(resource_dir: &Path) -> PathBuf {
    resource_dir.join("engine").join("ctx-engine")
}

fn main() {
    let log = std::env::var("SPIKE_LOG").unwrap_or_else(|_| "/tmp/ctx-spike.log".into());
    let supervisor = Supervisor {
        child: Arc::new(Mutex::new(None)),
        stopping: Arc::new(AtomicBool::new(false)),
        log: PathBuf::from(log),
    };
    let on_exit = supervisor.clone();

    tauri::Builder::default()
        .setup(move |app| {
            let exe = engine_path(&app.path().resource_dir()?);
            supervisor.log(&format!("app: engine at {}", exe.display()));
            let s = supervisor.clone();
            thread::spawn(move || s.run(exe));
            if let Some(secs) = std::env::var("SPIKE_EXIT_AFTER")
                .ok()
                .and_then(|v| v.parse().ok())
            {
                let handle = app.handle().clone();
                thread::spawn(move || {
                    thread::sleep(Duration::from_secs(secs));
                    handle.exit(0);
                });
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("tauri app")
        .run(move |_app, event| {
            if let RunEvent::Exit = event {
                on_exit.stop();
            }
        });
}
