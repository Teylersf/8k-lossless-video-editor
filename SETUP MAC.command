#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd -P)"
cd "$ROOT"
export PATH="/opt/homebrew/opt/ffmpeg-full/bin:/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:$PATH"
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || { printf 'Use a native Apple Silicon terminal.\n' >&2; exit 1; }
exec "${PYTHON3:-$(command -v python3.13)}" "$ROOT/setup_editor.py" "$@"
