# context-holder app

메뉴바에 상주하는 Tauri 2 앱. 엔진(`ctx serve --app`)을 띄우고, 엔진이 정한 메뉴 · 알림을 그리고, 엔진 화면을 창에 띄운다. 판단(언제 수집할지, 메뉴에 무엇을 보일지, 알릴지)은 모두 엔진이 한다.

```bash
cd app/src-tauri
cargo test && cargo clippy --all-targets -- -D warnings
cargo run            # 개발: engine/.venv/bin/ctx를 띄운다 (먼저 engine에서 uv sync)
CTX_ENGINE=/path/to/ctx cargo run   # 다른 엔진으로
```

| 주고받기 | 내용 |
|---|---|
| 엔진 stdout | 준비 줄 `{"port","token","url"}`, 그 뒤 `{"event":"status","icon","items"}` · `{"event":"notify","title","body","open"}` |
| 엔진 stdin | 메뉴 명령 한 줄: `sync_now`, `pause 1h`, `pause today`, `pause forever`, `resume` |

- 창을 닫아도 앱은 메뉴바에 남는다. 창이 열려 있을 때만 Dock 아이콘을 보인다.
- 화면(웹뷰)에는 IPC 권한을 주지 않는다. 키보드 단축키는 두지 않는다.
- 로그인 시 자동 실행은 release 빌드에서만 등록한다(`--background`로 창 없이 시작).
- 엔진이 비정상 종료하면 다시 띄운다(최대 3번). 앱이 끝나면 stdin을 닫아 엔진을 끈다.
- 메뉴바 아이콘은 단색 template 이미지다: `uv run --with pillow python app/scripts/tray_icons.py`.
- 엔진을 앱 안에 묶는 일(PyInstaller, resources)은 패키징에서 한다. 그 전에는 개발 엔진을 띄운다.
