#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_SOURCE="${ROOT}/build/macos/Things3 MCP Bridge.app"
APP_TARGET="${HOME}/Applications/Things3 MCP Bridge.app"
PLIST_TEMPLATE="${ROOT}/packaging/macos/com.rossshannon.things3-mcp.bridge.plist.template"
PLIST_TARGET="${HOME}/Library/LaunchAgents/com.rossshannon.things3-mcp.bridge.plist"
LOG_DIR="${HOME}/Library/Logs/Things3-MCP"
DATA_FOLDER="${THINGS3_MCP_DATA_FOLDER:-}"

if [[ ! -d "${APP_SOURCE}" ]]; then
  echo "App bundle not found: ${APP_SOURCE}. Run build/sign scripts first." >&2
  exit 1
fi

SIGNATURE="$(codesign -dv "${APP_SOURCE}" 2>&1 || true)"
if ! grep -Fq "Identifier=com.rossshannon.things3-mcp.bridge" <<<"${SIGNATURE}"; then
  echo "The bridge app is not signed with the expected bundle identifier." >&2
  echo "Run scripts/sign_bridge_app.sh before installing." >&2
  exit 1
fi

if grep -Fq "Signature=adhoc" <<<"${SIGNATURE}"; then
  cat >&2 <<'EOF'
Warning: installing an ad-hoc signed bridge app.

This can work for quick testing, but it is not the recommended Full Disk Access
setup. For reliable repeated access, sign with a local Code Signing certificate:
  scripts/sign_bridge_app.sh --identity "Things3 MCP Local"
EOF
fi

if [[ -n "${DATA_FOLDER}" && ! "${DATA_FOLDER}" =~ ^ThingsData-[A-Za-z0-9_-]+$ ]]; then
  echo "Invalid THINGS3_MCP_DATA_FOLDER=${DATA_FOLDER}" >&2
  echo "Expected a folder name like ThingsData-ABC123." >&2
  exit 1
fi

mkdir -p "${HOME}/Applications" "${HOME}/Library/LaunchAgents" "${LOG_DIR}"
rm -rf "${APP_TARGET}"
cp -R "${APP_SOURCE}" "${APP_TARGET}"
sed \
  -e "s#__HOME__#${HOME}#g" \
  -e "s#__THINGS3_MCP_DATA_FOLDER__#${DATA_FOLDER}#g" \
  "${PLIST_TEMPLATE}" > "${PLIST_TARGET}"
launchctl unload "${PLIST_TARGET}" >/dev/null 2>&1 || true
launchctl load "${PLIST_TARGET}"
echo "Installed LaunchAgent at ${PLIST_TARGET}"
if [[ -n "${DATA_FOLDER}" ]]; then
  echo "Configured LaunchAgent THINGS3_MCP_DATA_FOLDER=${DATA_FOLDER}"
fi
cat <<EOF

Now grant macOS privacy access to the installed app:
  ${APP_TARGET}

System Settings -> Privacy & Security -> Full Disk Access:
  1. Remove any older Things3 MCP Bridge entry.
  2. Add ${APP_TARGET}.
  3. Toggle it on.
  4. Restart the LaunchAgent by re-running this install script.

Then verify:
  uv run python scripts/check_bridge.py --snapshot
EOF
