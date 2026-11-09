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
| 엔진 stdout | 준비 줄 `{"port","token","url"}`, 그 뒤 `{"event":"status","icon","items"}` · `{"event":"notify","title","body","open"}` · `{"event":"autostart","enabled"}` |
| 엔진 stdin | 메뉴 명령 한 줄: `sync_now`, `pause 1h`, `pause today`, `pause forever`, `resume` |

- 창을 닫아도 앱은 메뉴바에 남는다. 창이 열려 있을 때만 Dock 아이콘을 보인다.
- 화면(웹뷰)에는 IPC 권한을 주지 않는다. 키보드 단축키는 두지 않는다.
- 로그인 시 자동 실행은 엔진 설정(`autostart` 이벤트)을 따르고, release 빌드에서만 등록한다(`--background`로 창 없이 시작).
- 창 안에서는 엔진 화면(`http://127.0.0.1:<port>`)만 연다. 원문 · GitHub 승인 같은 바깥 주소와 `target=_blank` 링크는 기본 브라우저로 연다.
- 엔진이 비정상 종료하면 다시 띄운다(최대 3번). 앱이 끝나면 stdin을 닫아 엔진을 끈다.
- 메뉴바 아이콘은 단색 template 이미지다: `uv run --with pillow python app/scripts/tray_icons.py`.
- 엔진은 `CTX_ENGINE` → 앱 안 `Resources/engine/ctx-engine` → 개발 엔진(`engine/.venv/bin/ctx`) 순으로 찾는다.

## 빌드

```bash
scripts/build-app.sh            # 엔진 묶기(PyInstaller onedir) → 앱 묶기(Tauri) → ad-hoc 서명
scripts/build-app.sh --install  # /Applications에 복사
```

- 엔진 스펙: `engine/packaging/ctx-engine.spec`. 묶은 엔진 약 210MB(onnxruntime 포함), 앱 약 240MB. 검색 모델(약 570MB)은 넣지 않고 설정 후 내려받는다.
- 묶을 때만 `src-tauri/tauri.bundle.conf.json`(엔진 resources · ad-hoc 서명)을 더한다. 평소 `cargo build`와 CI는 엔진 없이 돈다.
- 앱 아이콘 원본은 `icons/app-icon.png`(`scripts/app_icon.py`), `src-tauri/icons/`는 `npx @tauri-apps/cli@2 icon`으로 만든다.
- 직접 빌드한 앱은 바로 열린다. 내려받은 앱을 Downloads에서 바로 열면 macOS가 임시 위치에서 실행하므로(App Translocation) MCP 등록을 막고 응용 프로그램 폴더로 옮기라고 안내한다.
