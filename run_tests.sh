#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

if [ -d "venv" ]; then
    source venv/bin/activate
fi

echo "Running Fuel Route API tests..."
python manage.py test routing
