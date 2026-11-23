"""Keeps likely secrets out of the review files.

Two layers: whole files that usually hold credentials are never read, and secret-looking values
inside ordinary code are replaced in place so line numbers stay valid.
"""

from __future__ import annotations

import fnmatch
import re

SENSITIVE_NAMES = (
    ".env",
    ".env.*",
    "*.env",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "credentials.json",
    "credentials.yml",
    "secrets.json",
    "secrets.yml",
    "secrets.yaml",
    "service-account*.json",
    "*.tfvars",
    "*.tfstate",
    "*.tfstate.backup",
    ".npmrc",
    ".netrc",
    ".pgpass",
    ".htpasswd",
)
SENSITIVE_FOLDERS = {".ssh", ".aws", ".gnupg"}


def is_sensitive_path(path: str) -> bool:
    parts = path.split("/")
    if SENSITIVE_FOLDERS.intersection(parts[:-1]):
        return True
    name = parts[-1].lower()
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in SENSITIVE_NAMES)


def _mask(kind: str) -> str:
    return f"«redacted:{kind}»"


# Tokens with a fixed, recognisable shape. The whole match is replaced.
TOKEN_PATTERNS = (
    ("aws-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("stripe-key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("api-key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
)

URL_PASSWORD = re.compile(r"(://[^/\s:@]+:)([^@\s/]{3,})(@)")

# name = "literal" where the name says it is a secret. A value read from ENV or params has no
# quote right after the separator, so real code that only passes secrets around is left alone.
SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)
    (\b[\w.-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential)[\w.-]*
      ["']?\s*(?:=>|:|=)\s*)
    (["'])([^"']{8,})\2
    """
)

PRIVATE_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
PRIVATE_KEY_END = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY-----")


class Redactor:
    """Redacts one file's lines in order, so a multi-line private key is caught to its end."""

    def __init__(self):
        self.count = 0
        self._inside_private_key = False

    def redact(self, text: str) -> str:
        if self._inside_private_key:
            self._inside_private_key = not PRIVATE_KEY_END.search(text)
            return self._replaced("private-key")
        if PRIVATE_KEY_BEGIN.search(text):
            self._inside_private_key = not PRIVATE_KEY_END.search(text)
            return self._replaced("private-key")
        return self._redact_values(text)

    def _replaced(self, kind: str) -> str:
        self.count += 1
        return _mask(kind)

    def _redact_values(self, text: str) -> str:
        for kind, pattern in TOKEN_PATTERNS:
            text = pattern.sub(lambda _match, kind=kind: self._replaced(kind), text)
        text = URL_PASSWORD.sub(lambda match: match.group(1) + self._replaced("password") + match.group(3), text)
        return SECRET_ASSIGNMENT.sub(
            lambda match: match.group(1) + match.group(2) + self._replaced("secret") + match.group(2), text
        )
