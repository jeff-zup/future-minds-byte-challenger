from __future__ import annotations

from typing import Any

from config import settings


class _LazyProviderClient:
    """
    Seleciona o cliente LLM real (Bedrock ou LiteLLM Gateway) de acordo com
    FINGUARD_LLM_PROVIDER, sem importar os dois módulos eagerly.

    Os nós do grafo (agente_1, agente_2, agente_3) importam apenas `llm_client`
    e chamam `invoke_json`/`invoke_text` — a troca de provedor é 100% via config,
    sem alterar código de negócio.
    """

    def _resolve(self):
        provider = settings.llm_provider
        if provider == "litellm":
            from llm.litellm_client import litellm_llm
            return litellm_llm
        if provider == "bedrock":
            from llm.bedrock import bedrock_llm
            return bedrock_llm
        raise ValueError(
            f"FINGUARD_LLM_PROVIDER inválido: '{provider}'. Use 'bedrock' ou 'litellm'."
        )

    def invoke_text(self, *args, **kwargs) -> str:
        return self._resolve().invoke_text(*args, **kwargs)

    def invoke_json(self, *args, **kwargs) -> dict[str, Any]:
        return self._resolve().invoke_json(*args, **kwargs)


# Singleton compartilhado entre todos os agentes — ponto único de troca de provedor.
llm_client = _LazyProviderClient()
