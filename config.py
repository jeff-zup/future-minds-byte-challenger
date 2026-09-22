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
    classifier_model: str = os.getenv("BEDROCK_MODEL_CLASSIFIER", "")  # Agente 1 — ex.: us.anthropic.claude-haiku-*
    risk_model: str = os.getenv("BEDROCK_MODEL_RISK", "")              # Agente 2 — ex.: google.gemma-3-4b-it
    report_model: str = os.getenv("BEDROCK_MODEL_REPORT", "")          # Agente 3 — ex.: amazon.nova-micro-v1:0
    mock_llm: bool = _bool("FINGUARD_MOCK_LLM", True)                  # True = sem chamadas AWS
    max_concurrency: int = int(os.getenv("FINGUARD_MAX_CONCURRENCY", "5"))  # Workers paralelos no abatch
    policy_path: Path = Path(os.getenv("FINGUARD_POLICY_PATH", "data/KS_POLITICA_INTERNA.pdf"))
    dataset_path: Path = Path(os.getenv("FINGUARD_DATASET_PATH", "data/dataset.csv"))
    output_dir: Path = Path("output")


# Singleton global — importado diretamente pelos módulos que precisam de configuração
settings = Settings()
