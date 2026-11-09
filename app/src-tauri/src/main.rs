//! context-holder 메뉴바 앱 (plan 2장). 엔진이 정한 메뉴 · 알림을 그리고, 엔진 화면을 창에 띄운다.
//!
//! - 창을 닫아도 앱은 메뉴바에 남는다. 창이 열려 있을 때만 Dock 아이콘을 보인다.
//! - 로그인 시 자동 실행은 엔진 설정을 따르고, release 빌드에서만 등록한다 (`--background`).
//! - 바깥 주소(원문, GitHub 승인)는 창 안에서 열지 않고 기본 브라우저로 연다.
//! - 화면에는 IPC 권한을 주지 않는다. 앱과 엔진은 stdin · stdout으로만 주고받는다.
//! - 키보드 단축키는 두지 않는다.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod engine;

use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::thread;

use engine::{Engine, Item, Line};
use tauri::image::Image;
use tauri::menu::{IsMenuItem, Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent, Wry};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};
use tauri_plugin_notification::NotificationExt;

const TRAY: &str = "main";
const WINDOW: &str = "main";
const BACKGROUND_ARG: &str = "--background";

#[derive(Default)]
struct Shell {
    base: Option<String>,    // http://127.0.0.1:<port>
    login: Option<String>,   // 토큰이 붙은 첫 주소 (쿠키로 바뀐다)
    pending: Option<String>, // 알림을 누르고 앱이 활성화되면 열 화면
}

type Shared = Arc<Mutex<Shell>>;

fn engine_path(app: &AppHandle) -> PathBuf {
    if let Ok(path) = std::env::var("CTX_ENGINE") {
        return PathBuf::from(path);
    }
    if let Ok(dir) = app.path().resource_dir() {
        let bundled = dir.join("engine").join("ctx-engine"); // 패키징(15번 커밋)에서 넣는다
        if bundled.exists() {
            return bundled;
        }
    }
    PathBuf::from(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../engine/.venv/bin/ctx"
    ))
}

fn tray_icon(state: &str) -> Image<'static> {
    let bytes: &[u8] = match state {
        "busy" => include_bytes!("../icons/tray-busy.png"),
        "error" => include_bytes!("../icons/tray-error.png"),
        "paused" => include_bytes!("../icons/tray-paused.png"),
        _ => include_bytes!("../icons/tray-normal.png"),
    };
    Image::from_bytes(bytes).expect("tray icon")
}

/// 엔진이 보낸 항목으로 메뉴를 만든다. 항목 id: "open:/경로#n" · "cmd:명령#n" · "quit".
fn build_menu(app: &AppHandle, items: &[Item]) -> tauri::Result<Menu<Wry>> {
    let built = build_items(app, items, &mut 0)?;
    let refs: Vec<&dyn IsMenuItem<Wry>> = built.iter().map(|b| b.as_ref()).collect();
    Menu::with_items(app, &refs)
}

fn build_items(
    app: &AppHandle,
    items: &[Item],
    n: &mut usize,
) -> tauri::Result<Vec<Box<dyn IsMenuItem<Wry>>>> {
    let mut out: Vec<Box<dyn IsMenuItem<Wry>>> = Vec::new();
    for item in items {
        *n += 1;
        if item.separator {
            out.push(Box::new(PredefinedMenuItem::separator(app)?));
        } else if !item.children.is_empty() {
            let children = build_items(app, &item.children, n)?;
            let refs: Vec<&dyn IsMenuItem<Wry>> = children.iter().map(|b| b.as_ref()).collect();
            out.push(Box::new(Submenu::with_items(
                app,
                &item.label,
                item.enabled,
                &refs,
            )?));
        } else {
            let id = if item.quit {
                "quit".to_string()
            } else if let Some(path) = &item.open {
                format!("open:{path}#{n}")
            } else if let Some(cmd) = &item.cmd {
                format!("cmd:{cmd}#{n}")
            } else {
                format!("text#{n}")
            };
            out.push(Box::new(MenuItem::with_id(
                app,
                id,
                &item.label,
                item.enabled,
                None::<&str>,
            )?));
        }
    }
    Ok(out)
}

fn starting_menu(app: &AppHandle) -> tauri::Result<Menu<Wry>> {
    build_menu(
        app,
        &[
            Item {
                label: "엔진을 띄우는 중".into(),
                enabled: false,
                ..empty()
            },
            Item {
                separator: true,
                ..empty()
            },
            Item {
                label: "context-holder 종료".into(),
                quit: true,
                ..empty()
            },
        ],
    )
}

fn empty() -> Item {
    Item {
        label: String::new(),
        enabled: true,
        separator: false,
        open: None,
        cmd: None,
        quit: false,
        children: vec![],
    }
}

/// 창을 열고 그 화면으로 간다. 창이 없으면 토큰 주소로 만든다.
fn open_window(app: &AppHandle, shell: &Shared, path: &str) {
    let (base, login) = {
        let s = shell.lock().unwrap();
        (s.base.clone(), s.login.clone())
    };
    let Some(base) = base else { return };
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);
    if let Some(window) = app.get_webview_window(WINDOW) {
        if let Ok(url) = format!("{base}{path}").parse() {
            let _ = window.navigate(url);
        }
        let _ = window.show();
        let _ = window.set_focus();
        return;
    }
    // 토큰은 어느 경로에 붙여도 쿠키로 바뀌고 주소에서 지워진다
    let token = login
        .as_deref()
        .and_then(|u| u.split("token=").nth(1))
        .unwrap_or("");
    create_window(app, &format!("{base}{path}?token={token}"), true);
}

/// 원문 · GitHub 승인 같은 바깥 주소는 기본 브라우저로 연다.
fn open_outside(url: &str) {
    let _ = std::process::Command::new("open").arg(url).spawn();
}

fn create_window(app: &AppHandle, url: &str, visible: bool) {
    let Ok(parsed) = url.parse() else { return };
    let built = WebviewWindowBuilder::new(app, WINDOW, WebviewUrl::External(parsed))
        .on_navigation(|url| {
            let internal = engine::is_internal(url.as_str());
            if !internal {
                open_outside(url.as_str());
            }
            internal
        })
        .on_new_window(|url, _| {
            // target=_blank 링크: 창을 새로 만들지 않고 바깥 주소는 브라우저로 연다
            if !engine::is_internal(url.as_str()) {
                open_outside(url.as_str());
            }
            tauri::webview::NewWindowResponse::Deny
        })
        .title("context-holder")
        .inner_size(1280.0, 840.0)
        .min_inner_size(900.0, 600.0)
        .visible(visible)
        .build();
    if let Ok(window) = built {
        let handle = app.clone();
        window.on_window_event(move |event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close(); // 창만 숨기고 앱은 메뉴바에 남는다
                if let Some(w) = handle.get_webview_window(WINDOW) {
                    let _ = w.hide();
                }
                #[cfg(target_os = "macos")]
                let _ = handle.set_activation_policy(tauri::ActivationPolicy::Accessory);
            }
        });
        if visible {
            let _ = window.set_focus();
        }
    }
}

fn on_engine_line(app: &AppHandle, shell: &Shared, line: Line, show_on_ready: bool) {
    match line {
        Line::Ready { url, base } => {
            {
                let mut s = shell.lock().unwrap();
                s.base = Some(base);
                s.login = Some(url.clone());
            }
            if let Some(window) = app.get_webview_window(WINDOW) {
                // 엔진을 다시 띄웠다: 새 토큰 주소로 다시 연다
                if let Ok(parsed) = url.parse() {
                    let _ = window.navigate(parsed);
                }
            } else {
                if show_on_ready {
                    #[cfg(target_os = "macos")]
                    let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);
                }
                create_window(app, &url, show_on_ready);
            }
        }
        Line::Status { icon, items } => {
            if let (Some(tray), Ok(menu)) = (app.tray_by_id(TRAY), build_menu(app, &items)) {
                let _ = tray.set_menu(Some(menu));
                let _ = tray.set_icon(Some(tray_icon(&icon)));
                let _ = tray.set_icon_as_template(true);
            }
        }
        Line::Autostart { enabled } => {
            // 개발 빌드는 등록하지 않는다 (개발 실행 파일을 로그인 항목에 넣지 않게)
            if !cfg!(debug_assertions) {
                let launcher = app.autolaunch();
                let _ = if enabled {
                    launcher.enable()
                } else {
                    launcher.disable()
                };
            }
        }
        Line::Notify { title, body, open } => {
            shell.lock().unwrap().pending = Some(open);
            let _ = app.notification().builder().title(title).body(body).show();
        }
    }
}

fn on_menu(app: &AppHandle, shell: &Shared, engine: &Engine, id: &str) {
    let id = id.split('#').next().unwrap_or(id);
    if id == "quit" {
        app.exit(0);
    } else if let Some(path) = id.strip_prefix("open:") {
        open_window(app, shell, path);
    } else if let Some(cmd) = id.strip_prefix("cmd:") {
        engine.send(cmd);
    }
}

fn main() {
    let shell: Shared = Arc::new(Mutex::new(Shell::default()));
    let engine_cell: Arc<Mutex<Option<Engine>>> = Arc::new(Mutex::new(None));
    let background = std::env::args().any(|a| a == BACKGROUND_ARG);

    let setup_shell = shell.clone();
    let setup_engine = engine_cell.clone();
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec![BACKGROUND_ARG]),
        ))
        .setup(move |app| {
            let handle = app.handle().clone();
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);

            let engine = Engine::new(engine_path(&handle));
            *setup_engine.lock().unwrap() = Some(engine.clone());
            let menu_shell = setup_shell.clone();
            let menu_engine = engine.clone();
            TrayIconBuilder::with_id(TRAY)
                .icon(tray_icon("normal"))
                .icon_as_template(true)
                .menu(&starting_menu(&handle)?)
                .show_menu_on_left_click(true)
                .on_menu_event(move |app, event| {
                    on_menu(app, &menu_shell, &menu_engine, event.id().as_ref())
                })
                .build(app)?;

            let line_shell = setup_shell.clone();
            let line_handle = handle.clone();
            thread::spawn(move || {
                engine.run(move |line| {
                    let app = line_handle.clone();
                    let shell = line_shell.clone();
                    let _ = line_handle.run_on_main_thread(move || {
                        on_engine_line(&app, &shell, line, !background)
                    });
                })
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("context-holder 앱을 만들지 못했다");

    app.run(move |app, event| match event {
        RunEvent::Reopen { .. } => {
            let target = shell
                .lock()
                .unwrap()
                .pending
                .take()
                .unwrap_or_else(|| "/today".into());
            open_window(app, &shell, &target);
        }
        RunEvent::Exit => {
            if let Some(engine) = engine_cell.lock().unwrap().as_ref() {
                engine.stop();
            }
        }
        _ => {}
    });
}
