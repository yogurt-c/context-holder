#!/usr/bin/env bash
# 묶은 엔진(engine/dist/ctx-engine)의 모든 Mach-O를 Developer ID로 서명한다 (hardened runtime · 타임스탬프).
# 공증은 앱 안의 모든 실행 파일이 이렇게 서명돼 있어야 받는다. Tauri는 Resources 안의 파일을 서명하지 않는다.
# 사용법: scripts/sign-engine.sh "Developer ID Application: 이름 (TEAMID)"
set -euo pipefail
identity="${1:?서명 인증서 이름을 주세요}"
root="$(cd "$(dirname "$0")/.." && pwd)"
engine="$root/engine/dist/ctx-engine"
entitlements="$root/app/src-tauri/engine.entitlements"
main="$engine/ctx-engine"

sign() { codesign --force --timestamp --options runtime --sign "$identity" "$@"; }

count=0
while IFS= read -r -d '' f; do
  [[ "$f" == "$main" ]] && continue
  if file -b "$f" | grep -q "Mach-O"; then
    sign "$f"
    count=$((count + 1))
  fi
done < <(find "$engine" -type f -print0)
sign --entitlements "$entitlements" "$main"  # 실행 파일은 마지막에, 권한과 함께
codesign --verify --strict "$main"
echo "서명했다: 라이브러리 ${count}개 + ctx-engine"
