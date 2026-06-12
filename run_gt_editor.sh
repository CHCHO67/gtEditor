#!/usr/bin/env bash
set -euo pipefail

# gtEditor one-click launcher.
# Run this file from anywhere: ./run_gt_editor.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON="$VENV_DIR/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "[gtEditor] Creating virtual environment at .venv ..."
  python3 -m venv "$VENV_DIR"
fi

# Install runtime dependencies automatically when the venv is new or incomplete.
if ! "$PYTHON" - <<'PY' >/dev/null 2>&1
import PIL
import PySide6
PY
then
  echo "[gtEditor] Installing required packages ..."
  "$PYTHON" -m pip install --upgrade pip
  "$PYTHON" -m pip install -e .
fi

mkdir -p "$SCRIPT_DIR/Output_data"

INPUT_ARGS=()
while IFS= read -r -d '' dir; do
  INPUT_ARGS+=(--input-data "$dir")
done < <(find "$SCRIPT_DIR/Input_data" -mindepth 1 -maxdepth 3 -type d \
  -exec test -d '{}/image' ';' \
  '(' -exec test -d '{}/json' ';' -o -exec test -d '{}/json_formatted' ';' ')' \
  -print0 | sort -z)

if [[ ${#INPUT_ARGS[@]} -eq 0 ]]; then
  echo "[gtEditor] No input datasets found. Expected folders like: Input_data/<name>/image and Input_data/<name>/json or json_formatted" >&2
  exit 1
fi

echo "[gtEditor] Launching gtEditor ..." >&2
PYTHONPATH="$SCRIPT_DIR/src${PYTHONPATH:+:$PYTHONPATH}" exec "$PYTHON" "$SCRIPT_DIR/src/cli.py" \
  "${INPUT_ARGS[@]}" \
  --output-data "$SCRIPT_DIR/Output_data" \
  "$@"
