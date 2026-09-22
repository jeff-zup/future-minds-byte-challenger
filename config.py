from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv

# Carrega variáveis do arquivo .env (se existir) antes de ler os valores
load_dotenv()


def _bool(name: str, default: bool) -> bool:
    """Converte variável de ambiente string para bool aceitando: 1, true, yes, y, on."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    """
    Configurações imutáveis carregadas do ambiente (.env ou variáveis do sistema).

    Padrão seguro: mock_llm=True garante que sem configuração explícita
    nenhuma chamada AWS é feita — útil em CI/CD e ambientes sem credenciais.
    """
    aws_region: str = os.getenv("AWS_REGION", "us-east-1")
    aws_profile: str | None = os.getenv("AWS_PROFILE") or None  # None = usa credencial padrão do ambiente

    # Provider ativo: "bedrock" (AWS Bedrock via boto3) ou "litellm" (AI Gateway / LiteLLM Proxy).
    llm_provider: str = os.getenv("FINGUARD_LLM_PROVIDER", "bedrock").strip().lower()

    # --- AWS Bedrock ---
    classifier_model: str = os.getenv("BEDROCK_MODEL_CLASSIFIER", "")  # Agente 1 — ex.: us.anthropic.claude-haiku-*
    risk_model: str = os.getenv("BEDROCK_MODEL_RISK", "")              # Agente 2 — ex.: google.gemma-3-4b-it
    report_model: str = os.getenv("BEDROCK_MODEL_REPORT", "")          # Agente 3 — ex.: amazon.nova-micro-v1:0

    # --- AI Gateway (LiteLLM Proxy) ---
    litellm_api_base: str = os.getenv("LITELLM_API_BASE", "")          # URL do gateway, ex.: https://ai-gateway.empresa.com
    litellm_api_key: str = os.getenv("LITELLM_TOKEN", "") or os.getenv("LITELLM_API_KEY", "")
    litellm_key_alias: str = os.getenv("LITELLM_KEY_ALIAS", "future-minds-013")
    litellm_budget_usd: float = float(os.getenv("LITELLM_BUDGET_USD", "6.54"))
    # Aliases de modelo configurados no gateway (roteiam para Claude via Bedrock por trás).
    litellm_model_classifier: str = os.getenv("LITELLM_MODEL_CLASSIFIER", "bedrock-anthropic-claude-haiku-4-5")
    litellm_model_risk: str = os.getenv("LITELLM_MODEL_RISK", "bedrock-anthropic-claude-sonnet-4-5")
    litellm_model_report: str = os.getenv("LITELLM_MODEL_REPORT", "bedrock-anthropic-claude-sonnet-4-5")
    # Caminho para um arquivo CA bundle (PEM) usado para validar o certificado TLS
    # do AI Gateway, útil quando o gateway usa um CA corporativo/interno que não
    # está na cadeia de confiança padrão do sistema. Deixe vazio para usar o padrão.
    litellm_ca_bundle: str = os.getenv("LITELLM_CA_BUNDLE", "")
    # Desliga a verificação de certificado (NÃO recomendado, apenas debug local).
    litellm_verify_ssl: bool = _bool("LITELLM_VERIFY_SSL", True)

    mock_llm: bool = _bool("FINGUARD_MOCK_LLM", True)                  # True = sem chamadas a provedor real
    max_concurrency: int = int(os.getenv("FINGUARD_MAX_CONCURRENCY", "5"))  # Workers paralelos no abatch
    policy_path: Path = Path(os.getenv("FINGUARD_POLICY_PATH", "data/KS_POLITICA_INTERNA.pdf"))
    dataset_path: Path = Path(os.getenv("FINGUARD_DATASET_PATH", "data/dataset.csv"))
    output_dir: Path = Path("output")

    @property
    def model_classifier(self) -> str:
        """Model ID do Agente 1, resolvido conforme o provider ativo (bedrock/litellm)."""
        return self.litellm_model_classifier if self.llm_provider == "litellm" else self.classifier_model

    @property
    def model_risk(self) -> str:
        """Model ID do Agente 2, resolvido conforme o provider ativo (bedrock/litellm)."""
        return self.litellm_model_risk if self.llm_provider == "litellm" else self.risk_model

    @property
    def model_report(self) -> str:
        """Model ID do Agente 3, resolvido conforme o provider ativo (bedrock/litellm)."""
        return self.litellm_model_report if self.llm_provider == "litellm" else self.report_model


# Singleton global — importado diretamente pelos módulos que precisam de configuração
settings = Settings()
