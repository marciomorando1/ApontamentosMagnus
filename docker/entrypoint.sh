#!/bin/sh
set -eu

python - <<'PY'
import os
import socket
import time
from urllib.parse import urlparse

database_url = os.getenv("DATABASE_URL", "")
parsed = urlparse(database_url)

if parsed.scheme.startswith("postgres") and parsed.hostname:
    host = parsed.hostname
    port = parsed.port or 5432
    deadline = time.time() + 60

    while True:
        try:
            with socket.create_connection((host, port), timeout=2):
                break
        except OSError:
            if time.time() >= deadline:
                raise
            print(f"Waiting for PostgreSQL at {host}:{port}...")
            time.sleep(2)
PY

python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec "$@"