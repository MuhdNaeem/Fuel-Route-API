#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

if [ -d "venv" ]; then
    source venv/bin/activate
fi

echo "Starting Fuel Route API server on http://127.0.0.1:8000/ ..."
python manage.py runserver 0.0.0.0:8000
