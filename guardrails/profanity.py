import re

# Lista de demonstração. Em produção, substituir por vocabulário aprovado pela organização
# e armazenado fora do código (arquivo externo ou variável de ambiente).
_TERMS = ["porra", "caralho", "merda", "foda", "puta"]
_PATTERN = re.compile(r"(?i)\b(" + "|".join(map(re.escape, _TERMS)) + r")\b")


def sanitize_profanity(text: str | None) -> str | None:
    """
    Remove palavrões do texto substituindo por "***".
    Aplicado apenas no campo resumo gerado pelo LLM no Agente 1.
    """
    if text is None:
        return None
    return _PATTERN.sub("***", text)
