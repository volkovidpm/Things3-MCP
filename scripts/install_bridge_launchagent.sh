#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_SOURCE="${ROOT}/build/macos/Things3 MCP Bridge.app"
APP_TARGET="${HOME}/Applications/Things3 MCP Bridge.app"
PLIST_TEMPLATE="${ROOT}/packaging/macos/com.rossshannon.things3-mcp.bridge.plist.template"
PLIST_TARGET="${HOME}/Library/LaunchAgents/com.rossshannon.things3-mcp.bridge.plist"
LOG_DIR="${HOME}/Library/Logs/Things3-MCP"

if [[ ! -d "${APP_SOURCE}" ]]; then
  echo "App bundle not found: ${APP_SOURCE}. Run build/sign scripts first." >&2
  exit 1
fi

mkdir -p "${HOME}/Applications" "${HOME}/Library/LaunchAgents" "${LOG_DIR}"
rm -rf "${APP_TARGET}"
cp -R "${APP_SOURCE}" "${APP_TARGET}"
sed "s#__HOME__#${HOME}#g" "${PLIST_TEMPLATE}" > "${PLIST_TARGET}"
launchctl unload "${PLIST_TARGET}" >/dev/null 2>&1 || true
launchctl load "${PLIST_TARGET}"
echo "Installed LaunchAgent at ${PLIST_TARGET}"
