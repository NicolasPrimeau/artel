#!/usr/bin/env bash
# Consistent read-only snapshot of the live store. sqlite .backup rather than cp:
# the database runs in WAL mode, so copying the file alone can catch a torn state.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
docker compose exec -T artel python -c "
import sqlite3
src = sqlite3.connect('/data/artel.db')
dst = sqlite3.connect('/tmp/snap.db')
src.backup(dst); dst.close(); src.close()
print('snapshot taken')
"
docker compose cp artel:/tmp/snap.db "${1:-/tmp/artel-ro.db}"
echo "snapshot -> ${1:-/tmp/artel-ro.db}"
