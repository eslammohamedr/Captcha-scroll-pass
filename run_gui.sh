#!/usr/bin/env bash
set -e

# Change directory to the folder containing this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "======================================================================"
echo " OPPO Device Verification Portal (Linux Launcher)"
echo "======================================================================"

# 1. Detect Python executable
PY_CMD=""

if [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
    PY_CMD="$SCRIPT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PY_CMD="python"
fi

if [ -z "$PY_CMD" ]; then
    echo "======================================================================"
    echo " [ERROR] Python is not installed or not found in system PATH."
    echo ""
    echo " Please install Python 3.10 or newer (e.g., sudo apt install python3 python3-pip python3-venv)"
    echo "======================================================================"
    exit 1
fi

echo "Using Python: $($PY_CMD --version) at $PY_CMD"

# 2. Check if all required libraries are installed
if ! "$PY_CMD" -c "import playwright, openpyxl, cv2, pandas, tqdm, requests, numpy" >/dev/null 2>&1; then
    echo "======================================================================"
    echo " First-time launch or missing libraries detected!"
    echo " Checking virtual environment and installing dependencies..."
    echo "======================================================================"
    echo ""
    
    # Create .venv if not using an active venv
    if [ "$PY_CMD" != "$SCRIPT_DIR/.venv/bin/python" ] && [ ! -d "$SCRIPT_DIR/.venv" ]; then
        echo "Creating virtual environment at .venv..."
        "$PY_CMD" -m venv "$SCRIPT_DIR/.venv"
        PY_CMD="$SCRIPT_DIR/.venv/bin/python"
    fi

    "$PY_CMD" -m pip install --upgrade pip
    "$PY_CMD" -m pip install -r requirements.txt
    
    echo ""
    echo "======================================================================"
    echo " Installing Playwright Chromium browser engine..."
    echo "======================================================================"
    echo ""
    "$PY_CMD" -m playwright install chromium || {
        echo " [Notice] Browser setup can also be run inside the app via 'Setup Browser'."
    }
    
    echo ""
    echo "======================================================================"
    echo " All libraries and dependencies verified!"
    echo "======================================================================"
    echo ""
fi

# 3. Check DISPLAY for GUI execution
if [ -z "$DISPLAY" ] && [ -z "$WAYLAND_DISPLAY" ]; then
    echo "[WARNING] No DISPLAY or WAYLAND_DISPLAY detected. GUI applications require an X11 or Wayland display server."
fi

# 4. Launch GUI application
echo "Launching GUI application..."
exec "$PY_CMD" gui_app.py "$@"
