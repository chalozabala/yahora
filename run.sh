#!/usr/bin/env bash
# One-command launcher: ./run.sh  →  open http://localhost:8080
set -e
cd "$(dirname "$0")"
python3 -m pip install -q -r backend/requirements.txt
echo ""
echo "  Sweeps corriendo en:  http://localhost:8080"
echo "  (Ctrl+C para frenar)"
echo ""
cd backend
exec python3 -m uvicorn main:app --host 0.0.0.0 --port "${PORT:-8080}"
