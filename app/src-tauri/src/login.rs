//! 로그인 시 자동 실행. 엔진 설정(`autostart` 이벤트)을 따르고 release 빌드에서만 등록한다.
//!
//! 앱 번들 안의 에이전트(`Contents/Library/LaunchAgents/dev.contextholder.app.agent.plist`)를
//! SMAppService로 등록한다. 앱 소속으로 등록돼 로그인 항목 알림 · 시스템 설정에 개발자 이름 대신
//! 앱 이름(Context Holder)이 나온다. 에이전트는 `--background`로 앱을 띄워 창 없이 시작한다.
//!
//! 0.1.0까지는 `~/Library/LaunchAgents/context-holder.plist`를 직접 썼다. 로그인 때 두 번 뜨지 않게 지운다.

use std::path::PathBuf;

pub const AGENT_PLIST: &str = "dev.contextholder.app.agent.plist";
const LEGACY_PLIST: &str = "Library/LaunchAgents/context-holder.plist";

/// 할 일: 등록 · 해제 · 그대로.
#[derive(Debug, PartialEq)]
pub enum Action {
    Register,
    Unregister,
    Keep,
}

/// 원하는 상태(enabled)와 지금 상태(registered: 등록돼 있거나 승인을 기다리는 중)로 할 일을 정한다.
/// 이미 맞으면 건드리지 않는다. 다시 등록할 때마다 macOS가 알림을 띄울 수 있다.
pub fn action(enabled: bool, registered: bool) -> Action {
    match (enabled, registered) {
        (true, false) => Action::Register,
        (false, true) => Action::Unregister,
        _ => Action::Keep,
    }
}

fn legacy_plist() -> Option<PathBuf> {
    std::env::var_os("HOME").map(|home| PathBuf::from(home).join(LEGACY_PLIST))
}

#[cfg(target_os = "macos")]
pub fn apply(enabled: bool) {
    use smappservice_rs::{AppService, ServiceStatus, ServiceType};

    if let Some(old) = legacy_plist().filter(|p| p.exists()) {
        let _ = std::fs::remove_file(old);
    }
    let service = AppService::new(ServiceType::Agent {
        plist_name: AGENT_PLIST,
    });
    let registered = matches!(
        service.status(),
        ServiceStatus::Enabled | ServiceStatus::RequiresApproval
    );
    let result = match action(enabled, registered) {
        Action::Register => service.register(),
        Action::Unregister => service.unregister(),
        Action::Keep => Ok(()),
    };
    if let Err(e) = result {
        eprintln!("로그인 항목을 바꾸지 못했다: {e:?}");
    }
}

#[cfg(not(target_os = "macos"))]
pub fn apply(_enabled: bool) {}

/// 앱이 이미 떠 있으면 None. 로그인 항목을 등록하면 launchd가 바로 한 번 더 띄우고,
/// LaunchServices를 거치지 않아 macOS가 막아 주지 않는다. 잠금은 프로세스가 끝날 때 풀린다.
pub fn single_instance(dir: &std::path::Path) -> Option<std::fs::File> {
    std::fs::create_dir_all(dir).ok()?;
    let file = std::fs::File::create(dir.join("app.lock")).ok()?;
    file.try_lock().ok()?;
    Some(file)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn changes_only_when_needed() {
        assert_eq!(action(true, false), Action::Register);
        assert_eq!(action(false, true), Action::Unregister);
        assert_eq!(action(true, true), Action::Keep);
        assert_eq!(action(false, false), Action::Keep);
    }

    #[test]
    fn second_instance_is_refused() {
        let dir = std::env::temp_dir().join(format!("ch-lock-{}", std::process::id()));
        let first = single_instance(&dir);
        assert!(first.is_some());
        assert!(single_instance(&dir).is_none());
        drop(first);
        assert!(single_instance(&dir).is_some());
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn legacy_plist_is_in_home() {
        let path = legacy_plist().unwrap();
        assert!(path.ends_with("Library/LaunchAgents/context-holder.plist"));
    }
}
