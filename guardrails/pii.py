import re


# Padrões de PII financeira brasileira substituídos antes de qualquer persistência em disco.
# Ordem importa: CPF com pontuação deve ser capturado antes do padrão de 11 dígitos soltos.
PATTERNS = [
    (re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"), "[CPF]"),           # CPF formatado: 123.456.789-00
    (re.compile(r"\b\d{11}\b"), "[CPF/ID]"),                             # 11 dígitos sem formatação
    (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "[DADO_CARTAO]"),          # Número de cartão (13-19 dígitos)
    (re.compile(r"(?i)\b(?:conta|ag[eê]ncia)\s*[:#-]?\s*\d{3,}\b"), "[DADO_BANCARIO]"),  # Conta/agência
]


def mask_pii(text: str | None) -> str | None:
    """
    Substitui dados pessoais sensíveis por placeholders nos campos de texto.
    Aplicado tanto no resumo gerado pelo LLM quanto no texto original antes de salvar em disco.
    """
    if text is None:
        return None
    masked = text
    for pattern, replacement in PATTERNS:
        masked = pattern.sub(replacement, masked)
    return masked
