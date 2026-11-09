#!/usr/bin/env bash
# Context Holder.app을 이 Mac에서 빌드한다: 엔진 묶기(PyInstaller) → 앱 묶기(Tauri) → ad-hoc 서명.
# 사용법: scripts/build-app.sh            결과: app/src-tauri/target/release/bundle/macos/Context Holder.app
#         scripts/build-app.sh --install  /Applications에 복사한다
# 직접 빌드한 앱은 격리 표시가 없어 바로 열린다. 배포용 서명 · 공증은 릴리스 워크플로우에서 한다.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.cargo/bin:$PATH"

echo "==> 엔진 묶기"
cd "$root/engine"
uv sync --locked --quiet
uv run pyinstaller packaging/ctx-engine.spec --noconfirm --log-level WARN \
  --distpath dist --workpath build/pyinstaller
dist/ctx-engine/ctx-engine --version

echo "==> 앱 묶기"
cd "$root/app"
npx -y @tauri-apps/cli@2 build --bundles app --config src-tauri/tauri.bundle.conf.json

app="$root/app/src-tauri/target/release/bundle/macos/Context Holder.app"
echo "==> ad-hoc 서명"
codesign --force --deep --sign - "$app"
codesign --verify --deep --strict "$app"
du -sh "$app"

if [[ "${1:-}" == "--install" ]]; then
  rm -rf "/Applications/Context Holder.app"
  if [[ -d "/Applications/context-holder.app" ]]; then  # 이름을 바꾸기 전 직접 빌드해 넣은 앱
    echo "옛 이름의 앱(/Applications/context-holder.app)이 있다. 확인한 뒤 지운다."
  fi
  cp -R "$app" /Applications/
  echo "설치했다: /Applications/Context Holder.app"
else
  echo "$app"
fi
