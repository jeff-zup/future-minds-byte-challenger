from __future__ import annotations

import json
import re
import time
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from config import settings


class LLMError(RuntimeError):
    """Exceção unificada para falhas de comunicação com o AWS Bedrock."""
    pass


def _extract_json(text: str) -> dict[str, Any]:
    """
    Extrai JSON da resposta do LLM com tolerância a formatação extra.

    Estratégia em dois passos:
        1. Remove blocos de código markdown (```json ... ```) e tenta json.loads direto.
        2. Se falhar, localiza o primeiro { e o último } e tenta parsear só esse trecho.
           Cobre casos onde o LLM adiciona texto antes/depois do JSON.
    """
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def _is_throttling(exc: ClientError) -> bool:
    code = exc.response.get("Error", {}).get("Code", "")
    return code in {"ThrottlingException", "TooManyRequestsException", "ServiceUnavailableException"}


class BedrockLLM:
    """
    Cliente lazy para AWS Bedrock Runtime com retry automático para throttling.

    O cliente boto3 é criado na primeira chamada para evitar falha no import
    quando as credenciais AWS não estão disponíveis (ex.: modo mock).
    """

    # Backoff exponencial: 2s, 4s, 8s, 16s entre tentativas
    _RETRY_BASE_DELAY = 2.0
    _MAX_RETRIES = 4

    def __init__(self) -> None:
        self._client = None

    def _client_or_create(self):
        if self._client is not None:
            return self._client

        session_kwargs = {}
        if settings.aws_profile:
            session_kwargs["profile_name"] = settings.aws_profile

        session = boto3.Session(**session_kwargs)
        self._client = session.client("bedrock-runtime", region_name=settings.aws_region)
        return self._client

    def invoke_text(self, model_id: str, system_prompt: str, user_prompt: str, max_tokens: int = 1600) -> str:
        """
        Chama o Bedrock Converse API e retorna o texto da resposta.

        Retenta automaticamente em ThrottlingException com backoff exponencial
        (2s → 4s → 8s → 16s) antes de desistir.

        temperature=0.0 garante respostas determinísticas e reproduzíveis entre runs.
        Modelos novos exigem inference profile ID com prefixo regional (ex.: us.anthropic...).
        """
        if not model_id:
            raise LLMError("Model ID do Bedrock não configurado.")

        last_exc: Exception | None = None
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                response = self._client_or_create().converse(
                    modelId=model_id,
                    system=[{"text": system_prompt}],
                    messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                    inferenceConfig={"temperature": 0.0, "maxTokens": max_tokens},
                )
                blocks = response["output"]["message"]["content"]
                return "\n".join(block.get("text", "") for block in blocks if "text" in block).strip()

            except ClientError as exc:
                if _is_throttling(exc) and attempt < self._MAX_RETRIES:
                    delay = self._RETRY_BASE_DELAY * (2 ** attempt)
                    time.sleep(delay)
                    last_exc = exc
                    continue
                raise LLMError(f"Falha ao chamar AWS Bedrock: {exc}") from exc

            except BotoCoreError as exc:
                raise LLMError(f"Falha ao chamar AWS Bedrock: {exc}") from exc

        raise LLMError(f"Limite de tentativas atingido após throttling: {last_exc}") from last_exc

    def invoke_json(self, model_id: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """Chama invoke_text e faz parse do JSON retornado pelo LLM."""
        return _extract_json(self.invoke_text(model_id, system_prompt, user_prompt))


# Singleton compartilhado entre todos os agentes para reutilizar a conexão boto3
bedrock_llm = BedrockLLM()
