#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd -P)"
cd "$ROOT"
export PATH="/opt/homebrew/opt/ffmpeg-full/bin:/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:$PATH"
[[ "$(uname -s)" == Darwin ]] || { printf 'This launcher requires macOS.\n' >&2; exit 1; }
if [[ -x "$ROOT/.venv-editor/bin/python" ]]; then PYTHON="$ROOT/.venv-editor/bin/python"; else PYTHON="$(command -v python3.13)"; fi
exec "$PYTHON" "$ROOT/launch.py" "$@"
