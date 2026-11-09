# Tauri sidecar 스파이크

Tauri 앱이 PyInstaller onedir 엔진을 resources에서 띄우고 감시할 수 있는지 미리 확인한다.
엔진은 [`engine/serve.py`](engine/serve.py)(pygit2를 불러오고 상태를 한 줄씩 출력),
앱은 [`src-tauri/src/main.rs`](src-tauri/src/main.rs)(띄우기 · 로그 · 재시작 · 종료)다.

```bash
# 1. 엔진 묶기 (spikes/sidecar 환경의 PyInstaller를 쓴다)
cd spikes/sidecar
uv run pyinstaller --onedir --name ctx-engine --hidden-import _cffi_backend \
  --collect-binaries pygit2 --noconfirm --distpath ../tauri-sidecar/src-tauri/binaries \
  --workpath ../tauri-sidecar/build/work --specpath ../tauri-sidecar/build ../tauri-sidecar/engine/serve.py

# 2. 앱 빌드 · 실행 (SPIKE_EXIT_AFTER초 뒤 스스로 끝난다)
cd ../tauri-sidecar/src-tauri
cargo build
SPIKE_LOG=/tmp/ctx-spike.log SPIKE_CRASH_ONCE=/tmp/spike-crash SPIKE_EXIT_AFTER=6 ./target/debug/ctx-spike

# 3. .app 번들
npx @tauri-apps/cli@2 build --bundles app
```

## 결과 (2026-11-09, macOS arm64, Rust 1.99, Tauri 2.12)

| 확인 | 결과 |
|---|---|
| resources의 onedir 엔진 실행 | 개발 빌드와 `.app` 모두 `resource_dir()/engine/ctx-engine`으로 실행된다 |
| 엔진 시작 시간 | `.app`에서 0.06초. 복사 직후 첫 실행만 약 1.1초 (macOS의 dylib 검사) |
| 비정상 종료 후 재시작 | 종료 코드 3으로 죽은 엔진을 0.5초 뒤 다시 띄운다. 재시작은 최대 3번, 점점 늦게 |
| 앱이 정상 종료 | `RunEvent::Exit`에서 엔진을 끈다. 남는 프로세스 없음 |
| 앱이 강제 종료 (`kill -9`) | 엔진도 1초 안에 끝난다. 남는 프로세스 없음 |
| 빌드 시간 · 크기 | 처음 개발 빌드 52초, release 1분. `.app` 42MB (엔진 32MB) |

## 본 구현에 반영할 것

- **엔진은 stdin EOF를 정상 종료로 처리한다.** 앱이 강제로 죽으면 엔진은 stdin EOF보다 먼저 stdout 쓰기에서 `BrokenPipeError`로 죽었다. 남지는 않지만 오류 로그가 남는다. stdin을 감시하고 stdout 쓰기 실패도 조용히 끝내게 한다.
- **resources 복사는 심볼릭 링크를 실제 파일로 푼다.** onedir의 `_internal/`에 있는 dylib 링크 3개(libgit2, libssh2, libcrypto)가 복사본이 되어 엔진이 26MB에서 32MB로 커졌다. 동작에는 문제없다. 크기를 줄이려면 묶은 뒤 링크 대상을 정리하거나 직접 복사하는 빌드 단계를 둔다.
- 서명 없는 앱의 Gatekeeper 격리 속성(quarantine) 확인은 패키징 커밋에서 한다 ([spikes/sidecar](../sidecar/README.md)).
