#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="Things3 MCP Bridge"
BUILD_DIR="${ROOT}/build/macos"
APP_DIR="${BUILD_DIR}/${APP_NAME}.app"

rm -rf "${APP_DIR}" "${BUILD_DIR}/pyinstaller-work"

ENTRYPOINT="${BUILD_DIR}/bridge_entry.py"
cat > "${ENTRYPOINT}" <<'PY'
from things3_mcp_bridge.server import main

if __name__ == "__main__":
    raise SystemExit(main())
PY

uv run --locked pyinstaller \
  --clean \
  --noconfirm \
  --onedir \
  --windowed \
  --name "${APP_NAME}" \
  --osx-bundle-identifier com.rossshannon.things3-mcp.bridge \
  --distpath "${BUILD_DIR}" \
  --workpath "${BUILD_DIR}/pyinstaller-work" \
  --collect-submodules things \
  --collect-submodules things3_mcp \
  --collect-submodules things3_mcp_bridge \
  "${ENTRYPOINT}" >/dev/null

/usr/libexec/PlistBuddy -c "Add :LSBackgroundOnly bool true" "${APP_DIR}/Contents/Info.plist" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Set :LSBackgroundOnly true" "${APP_DIR}/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName Things3 MCP Bridge" "${APP_DIR}/Contents/Info.plist"

echo "Built ${APP_DIR}"
