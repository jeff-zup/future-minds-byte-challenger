from __future__ import annotations

import json
import re
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from config import settings


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> dict[str, Any]:
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


class BedrockLLM:
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
        if not model_id:
            raise LLMError("Model ID do Bedrock não configurado.")

        try:
            response = self._client_or_create().converse(
                modelId=model_id,
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                inferenceConfig={"temperature": 0.0, "maxTokens": max_tokens},
            )
        except (BotoCoreError, ClientError) as exc:
            raise LLMError(f"Falha ao chamar AWS Bedrock: {exc}") from exc

        blocks = response["output"]["message"]["content"]
        return "\n".join(block.get("text", "") for block in blocks if "text" in block).strip()

    def invoke_json(self, model_id: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        return _extract_json(self.invoke_text(model_id, system_prompt, user_prompt))


bedrock_llm = BedrockLLM()
