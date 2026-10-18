#!/usr/bin/env bash
# ======================================================================
#  OPPO Sales Activation Monitor - Linux Web Server Launcher
# ======================================================================
set -e
cd "$(dirname "$0")"

echo "======================================================================"
echo " OPPO Sales Activation Monitor & Server"
echo "======================================================================"

# Determine Python binary
if [ -f ".venv/bin/python" ]; then
    PYTHON_CMD=".venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
else
    PYTHON_CMD="python"
fi

echo "Using Python: $($PYTHON_CMD --version) at $PYTHON_CMD"

# Get Local IP for phone access
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")

echo ""
echo "📱 MOBILE ACCESS URL: http://${LOCAL_IP}:8000"
echo "💻 LOCAL ACCESS URL:  http://localhost:8000"
echo "📑 API DOCS URL:      http://localhost:8000/api/docs"
echo ""
echo "Press Ctrl+C to stop the server."
echo "======================================================================"

exec $PYTHON_CMD -m uvicorn server:app --host 0.0.0.0 --port 8000 --log-level info
