from __future__ import annotations

import ssl
import time
from typing import Any

from config import settings
from llm.utils import GuardrailBlockedError, LLMError, extract_json

# openai é opcional: só é importado quando o provider realmente é usado,
# para não quebrar ambientes (ex.: mock/Bedrock) que não instalaram o pacote.
try:
    from openai import APIStatusError, OpenAI
except ImportError:  # pragma: no cover - fallback tratado em runtime
    OpenAI = None
    APIStatusError = Exception

try:
    import httpx
except ImportError:  # pragma: no cover - httpx é dependência transitiva do openai
    httpx = None


def _build_ssl_context(ca_bundle_path: str) -> ssl.SSLContext:
    """
    Cria um SSLContext confiando no CA bundle informado.

    Arquivos ".cer"/".crt" exportados de ferramentas Windows/Java costumam
    estar em formato DER (binário), enquanto `ssl`/`httpx` só carregam PEM
    (texto, com "-----BEGIN CERTIFICATE-----") nativamente. Aqui detectamos
    o formato e convertemos DER -> PEM em memória quando necessário, para que
    o usuário possa apontar direto para o .cer exportado sem conversão manual.
    """
    context = ssl.create_default_context()
    try:
        # Tenta carregar como PEM diretamente (caso mais comum).
        context.load_verify_locations(cafile=ca_bundle_path)
        return context
    except ssl.SSLError:
        pass

    # Fallback: assume DER binário e converte para PEM em memória.
    with open(ca_bundle_path, "rb") as fh:
        der_bytes = fh.read()
    pem_text = ssl.DER_cert_to_PEM_cert(der_bytes)
    context.load_verify_locations(cadata=pem_text)
    return context


_THROTTLING_STATUS_CODES = {429, 500, 502, 503, 504}

# Sentinels conhecidos que alguns gateways/guardrails (ex.: Bedrock Guardrails
# atrás do LiteLLM Proxy) retornam no lugar do conteúdo real quando um filtro
# de segurança (prompt injection, jailbreak, conteúdo sensível) é acionado.
# Nesses casos o content não é vazio nem JSON — é literalmente esse texto.
_GUARDRAIL_BLOCK_MARKERS = {
    "attack_instruction_request_detected",
    "prompt_attack",
    "prompt_injection_detected",
    "content_filtered",
    "blocked_by_guardrail",
}


def _is_throttling(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code in _THROTTLING_STATUS_CODES:
        return True
    message = str(exc).lower()
    markers = ("rate limit", "ratelimit", "429", "throttl", "overloaded", "503")
    return any(marker in message for marker in markers)


class LiteLLMClient:
    """
    Cliente para o AI Gateway (LiteLLM Proxy), usado como substituto do AWS Bedrock.

    Compatível com a mesma interface pública de `BedrockLLM` (invoke_text/invoke_json),
    permitindo trocar o provedor apenas via configuração (FINGUARD_LLM_PROVIDER),
    sem alterar os nós do grafo (agente_1, agente_2, agente_3).

    Usa o SDK oficial `openai`, pois o gateway (LiteLLM Proxy) expõe uma API
    100% compatível com o formato OpenAI (/chat/completions) — não é necessário
    o SDK `litellm` para isso, que é mais pesado e trata roteamento multi-provider
    do lado do cliente (desnecessário quando já existe um proxy fazendo esse papel).

    A autenticação usa key_alias + token do gateway (LITELLM_TOKEN) e aponta para
    o api_base do proxy (LITELLM_API_BASE). Os "model" enviados são os aliases
    configurados no gateway (ex.: bedrock-anthropic-claude-haiku-4-5), que o LiteLLM
    Proxy roteia internamente para o provedor real (neste caso, AWS Bedrock).
    """

    _RETRY_BASE_DELAY = 2.0
    _MAX_RETRIES = 4

    def __init__(self) -> None:
        if OpenAI is None:
            raise LLMError(
                "Pacote 'openai' não instalado. Rode `pip install openai` "
                "ou use FINGUARD_LLM_PROVIDER=bedrock/mock."
            )
        if not settings.litellm_api_base:
            raise LLMError("LITELLM_API_BASE não configurado.")
        if not settings.litellm_api_key:
            raise LLMError("LITELLM_TOKEN não configurado.")

        default_headers = (
            {"X-Litellm-Key-Alias": settings.litellm_key_alias} if settings.litellm_key_alias else None
        )
        # O SDK OpenAI concatena "/chat/completions" ao base_url — o LiteLLM Proxy
        # expõe essa rota sob "/v1", então garantimos o sufixo aqui mesmo que o
        # .env aponte só para a raiz do gateway.
        api_base = settings.litellm_api_base.rstrip("/")
        if not api_base.endswith("/v1"):
            api_base = f"{api_base}/v1"

        # Define a verificação TLS: CA bundle customizado (ex.: certificado
        # corporativo interno), desabilitado (debug) ou padrão do sistema.
        http_client = None
        if httpx is not None:
            if settings.litellm_ca_bundle:
                try:
                    ssl_context = _build_ssl_context(settings.litellm_ca_bundle)
                except (FileNotFoundError, ssl.SSLError) as exc:
                    raise LLMError(
                        f"Falha ao carregar LITELLM_CA_BUNDLE '{settings.litellm_ca_bundle}': {exc}"
                    ) from exc
                http_client = httpx.Client(verify=ssl_context)
            elif not settings.litellm_verify_ssl:
                http_client = httpx.Client(verify=False)

        self._client = OpenAI(
            base_url=api_base,
            api_key=settings.litellm_api_key,
            default_headers=default_headers,
            http_client=http_client,
        )

    def invoke_text(self, model_id: str, system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> str:
        """
        Chama o AI Gateway (LiteLLM Proxy) via SDK OpenAI e retorna o texto da resposta.

        Retenta automaticamente em erros de throttling/rate limit com backoff exponencial
        (2s → 4s → 8s → 16s) antes de desistir, espelhando o comportamento do BedrockLLM.

        temperature=0.0 garante respostas determinísticas e reproduzíveis entre runs —
        importante porque os prompts reais (Agentes 1/2/3) exigem JSON estruturado e
        estável, diferente de um "Hello World" solto.

        max_tokens=2000 dá margem para o System Prompt extenso + resposta JSON completa.
        Valores baixos (ex.: 150, adequados só para um teste de conectividade trivial)
        fazem o modelo cortar a resposta no meio do JSON — ou até no meio do bloco
        ```json ``` — resultando em conteúdo vazio/inválido e falha ao parsear.

        O model_id é o alias configurado no gateway (ex.: bedrock-anthropic-claude-haiku-4-5),
        enviado diretamente como "model" — o gateway já sabe rotear para o provedor real.
        """
        if not model_id:
            raise LLMError("Model ID do LiteLLM não configurado.")

        last_exc: Exception | None = None
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                response = self._client.chat.completions.create(
                    model=model_id,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.0,
                    max_tokens=max_tokens,
                )
                choice = response.choices[0]
                content = (choice.message.content or "").strip()
                finish_reason = getattr(choice, "finish_reason", None)

                if content.lower() in _GUARDRAIL_BLOCK_MARKERS:
                    # O AI Gateway bloqueou a requisição por um guardrail de segurança
                    # (ex.: detector de prompt injection do Bedrock Guardrails), em vez
                    # de retornar o JSON esperado. Isso é comum quando o texto da
                    # reclamação do cliente contém frases que se parecem com tentativas
                    # de injeção de instrução (ex.: "ignore as instruções", "aja como...")
                    # mesmo sendo apenas a queixa legítima do cliente.
                    raise GuardrailBlockedError(
                        f"Requisição bloqueada por guardrail do AI Gateway para model_id='{model_id}' "
                        f"(marcador retornado: {content!r}). Isso indica um filtro de segurança "
                        "(ex.: detecção de prompt injection) no gateway/modelo, não um problema de "
                        "formatação do JSON. Ações possíveis: (1) revisar/ajustar a política de "
                        "guardrail configurada no alias do gateway para este caso de uso interno; "
                        "(2) delimitar claramente o texto do cliente como dado (ex.: tags "
                        "<reclamacao>...</reclamacao>) para reduzir falsos positivos; "
                        "(3) reportar ao time responsável pelo AI Gateway."
                    )

                if not content:
                    # O gateway respondeu 200 mas sem texto — pode ser corte por
                    # max_tokens (finish_reason="length"), filtro de conteúdo,
                    # ou o modelo ter usado o budget todo em "thinking"/reasoning.
                    # Retornar "" aqui geraria um JSONDecodeError confuso mais
                    # abaixo, então falhamos já com o motivo real.
                    raise LLMError(
                        f"Resposta vazia do AI Gateway (LiteLLM) para model_id='{model_id}' "
                        f"(finish_reason={finish_reason!r}, response_id={getattr(response, 'id', None)!r}). "
                        "Verifique se o alias do modelo está correto no gateway, se max_tokens é "
                        "suficiente e se há budget/cota disponível na LITELLM_KEY_ALIAS."
                    )
                if finish_reason == "length":
                    # Conteúdo não veio vazio, mas foi cortado no meio (ex.: JSON
                    # incompleto) — melhor falhar já com um erro claro do que deixar
                    # o extract_json estourar um JSONDecodeError sem contexto.
                    raise LLMError(
                        f"Resposta truncada por limite de tokens (finish_reason='length') "
                        f"para model_id='{model_id}'. Aumente max_tokens (atual={max_tokens})."
                    )
                return content

            except LLMError:
                raise
            except Exception as exc:
                if _is_throttling(exc) and attempt < self._MAX_RETRIES:
                    delay = self._RETRY_BASE_DELAY * (2 ** attempt)
                    time.sleep(delay)
                    last_exc = exc
                    continue
                raise LLMError(f"Falha ao chamar AI Gateway (LiteLLM): {exc}") from exc

        raise LLMError(f"Limite de tentativas atingido após throttling: {last_exc}") from last_exc

    def invoke_json(self, model_id: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """Chama invoke_text e faz parse do JSON retornado pelo LLM."""
        return extract_json(self.invoke_text(model_id, system_prompt, user_prompt))


# Singleton lazy: só valida configuração/importa litellm na primeira chamada real,
# evitando erro de import quando o provider ativo é "bedrock" ou "mock".
class _LazyLiteLLMClient:
    def __init__(self) -> None:
        self._instance: LiteLLMClient | None = None

    def _get(self) -> LiteLLMClient:
        if self._instance is None:
            self._instance = LiteLLMClient()
        return self._instance

    def invoke_text(self, *args, **kwargs) -> str:
        return self._get().invoke_text(*args, **kwargs)

    def invoke_json(self, *args, **kwargs) -> dict[str, Any]:
        return self._get().invoke_json(*args, **kwargs)


litellm_llm = _LazyLiteLLMClient()
