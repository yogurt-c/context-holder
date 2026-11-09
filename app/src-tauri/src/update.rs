//! 앱 업데이트 (Tauri 업데이터). 언제 확인할지 · 무엇을 보일지는 엔진이 정한다 (engine updates.py).
//!
//! 엔진이 `{"event":"update","action":"check"|"install"|"restart"}`를 보내면 여기서 하고,
//! 상태를 엔진 stdin에 `update {"state", "version", "done", "total", "error"}`로 돌려준다.
//! 개발 빌드는 확인하지 않는다 (`off`). 화면에 보일 오류 문구는 여기서 정하고, 자세한 원인은 stderr에 남긴다.

use std::sync::{Arc, Mutex};

use serde_json::json;
use tauri::AppHandle;
use tauri_plugin_updater::{Update, UpdaterExt};

use crate::engine::Engine;

const CHECK_FAILED: &str = "업데이트 서버에 연결하지 못했어요";
const INSTALL_FAILED: &str = "내려받거나 설치하지 못했어요. 다시 시도해 주세요";
const NOTHING_PENDING: &str = "받을 업데이트가 없어요. 다시 확인해 주세요";
const MB: u64 = 1_000_000;

/// 엔진 stdin으로 보낼 상태 한 줄.
pub fn state_line(state: &str, version: &str, done: u64, total: u64, error: &str) -> String {
    let value =
        json!({"state": state, "version": version, "done": done, "total": total, "error": error});
    format!("update {value}")
}

/// 진행률을 보낼 때인가: 1%가 바뀔 때마다. 전체 크기를 모르면 1MB마다.
pub fn should_report(last: u64, done: u64, total: u64) -> bool {
    match (done * 100).checked_div(total) {
        Some(percent) => percent != last * 100 / total,
        None => done / MB != last / MB,
    }
}

#[derive(Clone, Default)]
pub struct Updater {
    pending: Arc<Mutex<Option<Update>>>, // 확인해서 찾은 새 버전
}

impl Updater {
    pub fn handle(&self, app: &AppHandle, engine: &Engine, action: &str) {
        match action {
            "check" => self.check(app, engine),
            "install" => self.install(engine),
            "restart" => app.restart(),
            _ => {}
        }
    }

    fn check(&self, app: &AppHandle, engine: &Engine) {
        if cfg!(debug_assertions) {
            engine.send(&state_line("off", "", 0, 0, ""));
            return;
        }
        let (app, engine, pending) = (app.clone(), engine.clone(), self.pending.clone());
        tauri::async_runtime::spawn(async move {
            let found = match app.updater() {
                Ok(updater) => updater.check().await,
                Err(e) => Err(e),
            };
            let line = match found {
                Ok(Some(update)) => {
                    let version = update.version.clone();
                    *pending.lock().unwrap() = Some(update);
                    state_line("available", &version, 0, 0, "")
                }
                Ok(None) => state_line("idle", "", 0, 0, ""),
                Err(e) => {
                    eprintln!("업데이트를 확인하지 못했다: {e}");
                    state_line("error", "", 0, 0, CHECK_FAILED)
                }
            };
            engine.send(&line);
        });
    }

    fn install(&self, engine: &Engine) {
        let Some(update) = self.pending.lock().unwrap().clone() else {
            engine.send(&state_line("error", "", 0, 0, NOTHING_PENDING));
            return;
        };
        let engine = engine.clone();
        tauri::async_runtime::spawn(async move {
            let version = update.version.clone();
            let progress = engine.clone();
            let shown = version.clone();
            let (mut done, mut last) = (0u64, 0u64);
            progress.send(&state_line("downloading", &shown, 0, 0, ""));
            let result = update
                .download_and_install(
                    move |chunk, total| {
                        done += chunk as u64;
                        let total = total.unwrap_or(0);
                        if should_report(last, done, total) {
                            last = done;
                            progress.send(&state_line("downloading", &shown, done, total, ""));
                        }
                    },
                    || {},
                )
                .await;
            let line = match result {
                Ok(()) => state_line("ready", &version, 0, 0, ""),
                Err(e) => {
                    eprintln!("업데이트를 설치하지 못했다: {e}");
                    state_line("error", &version, 0, 0, INSTALL_FAILED)
                }
            };
            engine.send(&line);
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn state_line_is_json_after_prefix() {
        let line = state_line("downloading", "0.2.0", 5, 10, "");
        let body: serde_json::Value =
            serde_json::from_str(line.strip_prefix("update ").unwrap()).unwrap();
        assert_eq!(body["state"], "downloading");
        assert_eq!(body["version"], "0.2.0");
        assert_eq!(body["done"], 5);
        assert_eq!(body["total"], 10);
        assert!(!line.contains('\n'));
    }

    #[test]
    fn reports_each_percent_or_megabyte() {
        assert!(!should_report(0, 5, 1000)); // 0%
        assert!(should_report(0, 10, 1000)); // 1%
        assert!(!should_report(10, 19, 1000));
        assert!(!should_report(0, MB - 1, 0));
        assert!(should_report(MB - 1, MB, 0));
    }
}
