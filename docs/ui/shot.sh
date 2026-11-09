#!/bin/sh
# 사용법: sh docs/ui/shot.sh today [높이]  → docs/ui/screens/today.png
cd "$(dirname "$0")"
name="$1"; h="${2:-900}"
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars --blink-settings=preferredColorScheme=1 \
  --allow-file-access-from-files --virtual-time-budget=8000 --force-device-scale-factor=1.5 \
  --window-size=1440,"$h" --screenshot="screens/$name.png" "file://$PWD/$name.html" 2>/dev/null
echo "screens/$name.png"
