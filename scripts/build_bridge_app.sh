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

cat > "${MACOS}/Things3-MCP-bridge" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
exec Things3-MCP-bridge "$@"
SH
chmod +x "${MACOS}/Things3-MCP-bridge"

echo "Built ${APP_DIR}"
