import re


# Padrões de PII financeira brasileira substituídos antes de qualquer persistência em disco
# e antes de qualquer envio ao LLM (ver guardrails/README ou SECURITY.md).
#
# A ORDEM IMPORTA — padrões mais específicos primeiro, senão um padrão genérico
# consome o texto e impede o específico de casar:
#   1. E-mail antes de tudo: pode conter dígitos que dispararia os padrões numéricos.
#   2. CNPJ antes de CPF: ambos usam pontuação, o formato do CNPJ é mais específico.
#   3. CPF formatado antes do padrão de 11 dígitos soltos.
#   4. Telefone antes de cartão/CPF-solto: exige hífen, evita casar ano ou valor.
#   5. Cartão (13-19 dígitos) e 11 dígitos soltos por último, por serem os mais gulosos.
PATTERNS = [
    # E-mail — também cobre chave PIX no formato e-mail
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL]"),

    # CNPJ formatado: 12.345.678/0001-90
    (re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b"), "[CNPJ]"),

    # CPF formatado: 123.456.789-00
    (re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"), "[CPF]"),

    # Telefone BR, em duas formas mutuamente exclusivas:
    #   a) com DDD obrigatório: (11) 98765-4321 | 11 98765-4321 | +55 11 98765-4321
    #   b) celular sem DDD, que sempre começa com 9: 98765-4321
    # O DDD é obrigatório no ramo (a) e o ramo (b) exige o 9 inicial justamente para
    # não casar número de protocolo no formato "2024-1234".
    (
        re.compile(r"(?:\+?55[\s-]?)?(?:\(\d{2}\)\s?|\b\d{2}[\s-])\d{4,5}-\d{4}\b|\b9\d{4}-\d{4}\b"),
        "[TELEFONE]",
    ),

    # Chave PIX aleatória (UUID v4)
    (re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "[CHAVE_PIX]"),

    # CEP: 01310-100 (5 dígitos, hífen, 3 dígitos — não colide com telefone, que termina em 4)
    (re.compile(r"\b\d{5}-\d{3}\b"), "[CEP]"),

    # Número de cartão (13-19 dígitos, com ou sem separadores)
    (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "[DADO_CARTAO]"),

    # 11 dígitos sem formatação — CPF ou celular com DDD
    (re.compile(r"\b\d{11}\b"), "[CPF/ID]"),

    # Conta / agência bancária — inclui o dígito verificador ("conta 45678-9")
    (re.compile(r"(?i)\b(?:conta|ag[eê]ncia)\s*[:#-]?\s*\d{3,}(?:-\d{1,2})?\b"), "[DADO_BANCARIO]"),
]


def mask_pii(text: str | None) -> str | None:
    """
    Substitui dados pessoais sensíveis por placeholders nos campos de texto.

    Aplicado em três momentos:
        - no resumo gerado pelo LLM (Agente 1);
        - no texto original antes de gravar em disco;
        - no texto enviado ao LLM, para que PII não trafegue até a AWS.
    """
    if text is None:
        return None
    masked = text
    for pattern, replacement in PATTERNS:
        masked = pattern.sub(replacement, masked)
    return masked
