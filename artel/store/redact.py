import re

_KEY_FORMATS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private-key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    ),
    ("anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("secret-key", re.compile(r"\b(?:sk|rk)_[A-Za-z0-9_]{20,}")),
    ("google", re.compile(r"\bAIza[0-9A-Za-z_\-]{35,}")),
    ("aws", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("slack", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
)

_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_\-]*(?:API[_\-]?KEY|SECRET|PASSWORD|PASSWD|TOKEN|PRIVATE[_\-]KEY)[A-Z0-9_\-]*)"
    r"([\"']?\s*[:=]\s*)([\"']?)([^\s\"',;]{12,})"
)


def _looks_secret(value: str) -> bool:
    return (
        not value.startswith(("[redacted", "$", "<"))
        and any(c.isdigit() for c in value)
        and any(c.isalpha() for c in value)
    )


def redact(text: str) -> str:
    for kind, pattern in _KEY_FORMATS:
        text = pattern.sub(f"[redacted:{kind}]", text)
    return _ASSIGNMENT.sub(
        lambda m: (
            f"{m.group(1)}{m.group(2)}{m.group(3)}[redacted]"
            if _looks_secret(m.group(4))
            else m.group(0)
        ),
        text,
    )
