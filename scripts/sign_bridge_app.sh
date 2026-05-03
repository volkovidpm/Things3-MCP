#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="${ROOT}/build/macos/Things3 MCP Bridge.app"
IDENTITY="Things3 MCP Local"

if [[ "${1:-}" == "--identity" ]]; then
  IDENTITY="${2:?missing signing identity}"
fi

if [[ ! -d "${APP}" ]]; then
  echo "App bundle not found: ${APP}. Run scripts/build_bridge_app.sh first." >&2
  exit 1
fi

codesign --force --deep --sign "${IDENTITY}" "${APP}"
codesign -dv "${APP}"
echo "Signed ${APP} with identity: ${IDENTITY}"
