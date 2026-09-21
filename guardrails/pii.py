import re


PATTERNS = [
    (re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"), "[CPF]"),
    (re.compile(r"\b\d{11}\b"), "[CPF/ID]"),
    (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "[DADO_CARTAO]"),
    (re.compile(r"(?i)\b(?:conta|ag[eê]ncia)\s*[:#-]?\s*\d{3,}\b"), "[DADO_BANCARIO]"),
]


def mask_pii(text: str | None) -> str | None:
    if text is None:
        return None
    masked = text
    for pattern, replacement in PATTERNS:
        masked = pattern.sub(replacement, masked)
    return masked
