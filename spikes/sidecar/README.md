# sidecar 패키징 스파이크

PyInstaller로 묶은 엔진에서 pygit2(libgit2)가 동작하는지 미리 확인한다. Tauri가 띄우는 확인은 [tauri-sidecar](../tauri-sidecar/README.md)에서 했다.

```bash
cd spikes/sidecar
uv sync
uv run python spike.py --clone            # 묶기 전
uv run pyinstaller --onedir --name ctx-spike-dir \
  --hidden-import _cffi_backend --collect-binaries pygit2 \
  --distpath build/dist --workpath build/work --specpath build spike.py
build/dist/ctx-spike-dir/ctx-spike-dir --clone
```

## 결과 (2026-11-05, macOS arm64, Python 3.12, pygit2 libgit2 1.9.7)

| 항목 | 결과 |
|---|---|
| HTTPS · SSH 지원 | 둘 다 켜져 있음. GitHub HTTPS push에 별도 git 설치 불필요 |
| 저장소 생성 · 커밋 · 공개 저장소 clone | 묶은 바이너리에서 모두 동작 |
| 필요한 옵션 | `--hidden-import _cffi_backend` (없으면 `import pygit2`에서 실패), `--collect-binaries pygit2` |
| onefile | 11MB, 실행마다 압축 해제로 약 1.5초 추가 |
| onedir | 26MB, 첫 실행 1.8초, 이후 0.06초 |

## 결론

- 엔진은 **onedir**로 묶어 앱 번들의 resources에 넣는다. `ctx mcp`는 Claude Code 세션마다 새로 실행되므로 시작 시간이 중요하다.
- Tauri의 `externalBin`은 단일 파일을 기대하므로, onedir은 resources로 넣고 앱이 경로를 지정해 직접 실행한다.
- 격리 속성 확인(패키징 커밋): ad-hoc 서명 앱에 격리 표시를 붙이면 Gatekeeper가 앱 자체를 거절한다(`spctl`: rejected). 직접 빌드한 앱은 격리 표시가 없어 resources의 엔진까지 그대로 뜬다. 배포용은 Developer ID 서명 · 공증으로 간다.
