"""Masks secrets and personal data in text before it reaches Gemini, a log or a stored trace.

Pattern-based, so it catches well-formed values: credentials (API keys, tokens, JWTs, private keys, password=...),
emails, Indian phone numbers, Aadhaar, PAN, IFSC and long account-like numbers. It does NOT find people's names
or free-form confidential text, and it doesn't touch audio, video or images.

One Redactor per operation keeps placeholders stable: the same email is [EMAIL_1] every time it appears.
"""
import re

# Order matters: credentials first (a JWT contains dots, a key may contain digits), then the narrower IDs.
_PATTERNS = [
    ("SECRET", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("SECRET", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),          # JWT
    ("SECRET", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),                                               # Google API key
    ("SECRET", re.compile(r"\b(?:sk|pk|rk)[-_](?:live_|test_|proj-)?[A-Za-z0-9_-]{16,}")),           # sk-..., sk_live_...
    ("SECRET", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}|\bxox[abprs]-[A-Za-z0-9-]{10,}|\bAKIA[0-9A-Z]{16}\b")),
    # Label + value: only the value (group "v") is masked, so "password: [SECRET_1]" still reads naturally.
    ("SECRET", re.compile(r"(?i)\bbearer\s+(?P<v>[A-Za-z0-9._~+/=-]{16,})")),
    ("SECRET", re.compile(r"(?i)\b(?:password|passwd|pwd|passcode|secret|api[_ -]?key|token)\s*(?:[:=]|\bis\b)\s*"
                          r"(?P<v>[^\s,;]{4,})")),
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("PAN", re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")),
    ("IFSC", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
    ("AADHAAR", re.compile(r"\b[2-9][0-9]{3}[ -]?[0-9]{4}[ -]?[0-9]{4}\b")),
    ("PHONE", re.compile(r"(?:\+91[\s-]?|\b0)?\b[6-9][0-9]{4}[\s-]?[0-9]{5}\b")),
    ("ACCOUNT", re.compile(r"\b[0-9]{11,18}\b")),
]


class Redactor:
    def __init__(self):
        self._seen: dict[tuple[str, str], str] = {}
        self.found: dict[str, int] = {}   # kind -> how many distinct values were masked

    def _mask(self, kind: str, value: str) -> str:
        key = (kind, value)
        if key not in self._seen:
            self.found[kind] = self.found.get(kind, 0) + 1
            self._seen[key] = f"[{kind}_{self.found[kind]}]"
        return self._seen[key]

    def text(self, s: str) -> str:
        for kind, rx in _PATTERNS:
            s = rx.sub(lambda m, k=kind: self._replace(k, m), s)
        return s

    def _replace(self, kind: str, m: re.Match) -> str:
        if "v" not in m.re.groupindex:
            return self._mask(kind, m.group(0))
        start, end = m.span("v")
        whole = m.group(0)
        return whole[:start - m.start()] + self._mask(kind, m.group("v")) + whole[end - m.start():]

    def scrub(self, obj):
        """Every string inside dicts and lists, masked. Keys and non-strings stay as they are."""
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, dict):
            return {k: self.scrub(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.scrub(v) for v in obj]
        return obj


def redact(s: str) -> str:
    return Redactor().text(s)
