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

BRIDGE_BIN="${ROOT}/.venv/bin/Things3-MCP-bridge"
BRIDGE_PYTHON="${ROOT}/.venv/bin/python"
if [[ -x "${BRIDGE_BIN}" ]]; then
  cat > "${MACOS}/Things3-MCP-bridge" <<SH
#!/usr/bin/env bash
set -euo pipefail
exec "${BRIDGE_BIN}" "\$@"
SH
elif [[ -x "${BRIDGE_PYTHON}" ]]; then
  cat > "${MACOS}/Things3-MCP-bridge" <<SH
#!/usr/bin/env bash
set -euo pipefail
exec "${BRIDGE_PYTHON}" -m things3_mcp_bridge.server "\$@"
SH
else
  cat > "${MACOS}/Things3-MCP-bridge" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
exec Things3-MCP-bridge "$@"
SH
fi
chmod +x "${MACOS}/Things3-MCP-bridge"

echo "Built ${APP_DIR}"
