"""Dump the FastAPI OpenAPI schema to stdout."""

import json
import os

os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("AGENT_KEYS", "")
os.environ.setdefault("REGISTRATION_KEY", "")

from artel.server.app import app  # noqa: E402

print(json.dumps(app.openapi(), indent=2))
