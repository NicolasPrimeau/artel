import pytest

import artel.store.db as db_mod
from artel.store.redact import redact

from .conftest import HEADERS

GOOGLE = "AIza" + "Sy" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q"
SECRET = "sk_" + "Rm4r_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4"
OPENAI = "sk-" + "proj-" + "abcdefghijklmnopqrstuv123"
AWS = "AKIA" + "IOSFODNN7EXAMPLE"
GITHUB = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
FAKE = "fake" + "0123456789" + "abcdef"


@pytest.mark.parametrize(
    "raw,kind",
    [
        (f"maps key {GOOGLE} here", "google"),
        (f"STRIPE={SECRET}", "secret-key"),
        (f"export OPENAI_API_KEY={OPENAI}", "openai"),
        (f"aws {AWS}", "aws"),
        (f"push with {GITHUB}", "github"),
    ],
)
def test_known_key_formats_are_redacted(raw, kind):
    out = redact(raw)
    assert f"[redacted:{kind}]" in out
    assert GOOGLE not in out and SECRET not in out and OPENAI not in out


@pytest.mark.parametrize(
    "raw",
    [
        '"ARTEL_API_KEY": "' + FAKE + '"',
        "x-api-key: " + FAKE,
        "DB_PASSWORD='" + FAKE + "'",
    ],
)
def test_secret_assignments_are_redacted(raw):
    assert "[redacted]" in redact(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "Token: authentication happens first",
        "the password is required",
        "TOKEN=${GITHUB_TOKEN}",
        "API_KEY=<your key here>",
        "see the sk_live docs",
        "commit 61cd261 fixed the regret sensor",
    ],
)
def test_ordinary_text_is_untouched(raw):
    assert redact(raw) == raw


@pytest.mark.asyncio
async def test_capture_is_stored_redacted(client):
    r = await client.post(
        "/captures",
        json={"content": f"pasted {SECRET} by mistake", "session_id": "s1"},
        headers=HEADERS,
    )
    assert r.status_code == 201
    row = (
        db_mod.get_db()
        .execute("SELECT content FROM captures WHERE id=?", (r.json()["id"],))
        .fetchone()
    )
    assert SECRET not in row["content"]
    assert "[redacted:secret-key]" in row["content"]
