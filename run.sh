#!/usr/bin/env bash
# Run the agent using the project venv. Use from project root:
#   ./run.sh
# Or: bash run.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -d "venv" ]]; then
  echo "Creating venv..."
  python3 -m venv venv
fi

# Use venv's Python by path (avoids PATH/activate issues)
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python"
"$VENV_PYTHON" -m pip install -q -r requirements.txt
exec "$VENV_PYTHON" main.py "$@"
