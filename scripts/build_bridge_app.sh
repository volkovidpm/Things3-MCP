#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="Things3 MCP Bridge.app"
BUILD_DIR="${ROOT}/build/macos"
APP_DIR="${BUILD_DIR}/${APP_NAME}"
CONTENTS="${APP_DIR}/Contents"
MACOS="${CONTENTS}/MacOS"
RESOURCES="${CONTENTS}/Resources"
TEMPLATE="${ROOT}/packaging/macos/${APP_NAME}/Contents/Info.plist.template"

rm -rf "${APP_DIR}"
mkdir -p "${MACOS}" "${RESOURCES}"
cp "${TEMPLATE}" "${CONTENTS}/Info.plist"

ENTRYPOINT="${BUILD_DIR}/bridge_entry.py"
cat > "${ENTRYPOINT}" <<'PY'
from things3_mcp_bridge.server import main

if __name__ == "__main__":
    raise SystemExit(main())
PY

uv run --locked pyinstaller \
  --clean \
  --noconfirm \
  --onefile \
  --name Things3-MCP-bridge \
  --distpath "${MACOS}" \
  --workpath "${BUILD_DIR}/pyinstaller-work" \
  --collect-submodules things \
  --collect-submodules things3_mcp \
  --collect-submodules things3_mcp_bridge \
  "${ENTRYPOINT}" >/dev/null
chmod +x "${MACOS}/Things3-MCP-bridge"

echo "Built ${APP_DIR}"
