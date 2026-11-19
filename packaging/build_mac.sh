#!/usr/bin/env bash
# 构建 macOS 桌面版：dist/信息差.app + dist/信息差.dmg
# 用法：bash packaging/build_mac.sh [venv-python路径]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PY="${1:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
APP_NAME="信息差"

echo "▶ 使用 Python: $PY"
"$PY" -c "import PyInstaller, webview" 2>/dev/null || {
  echo "▶ 安装打包依赖 (pyinstaller/pywebview)"
  uv pip install --python "$PY" -e "$ROOT[desktop,pack]" 2>/dev/null \
    || "$PY" -m pip install -e "$ROOT[desktop,pack]"
}

echo "▶ PyInstaller 打包"
"$PY" -m PyInstaller --noconfirm --clean \
  --distpath "$ROOT/dist" --workpath "$ROOT/build" "$HERE/market_lens.spec"

APP="$ROOT/dist/$APP_NAME.app"
echo "▶ Ad-hoc 签名（免开发证书；分发给他人时见 README 的 Gatekeeper 说明）"
codesign --force --deep --sign - "$APP"

echo "▶ 制作 DMG"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
cp -R "$APP" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGING" \
  -ov -format UDZO "$ROOT/dist/$APP_NAME.dmg" >/dev/null

echo "✔ 完成："
ls -lh "$ROOT/dist" | grep -E "app|dmg"
