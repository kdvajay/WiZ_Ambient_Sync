#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export WIZ_AMBIENT_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/WiZAmbientSync/config.json"
mkdir -p "$(dirname "$WIZ_AMBIENT_CONFIG")"
if [ ! -f "$WIZ_AMBIENT_CONFIG" ]; then cp "$ROOT/app/config.json" "$WIZ_AMBIENT_CONFIG"; fi
exec python "$ROOT/app/main.py"
