from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    aws_region: str = os.getenv("AWS_REGION", "us-east-1")
    aws_profile: str | None = os.getenv("AWS_PROFILE") or None
    classifier_model: str = os.getenv("BEDROCK_MODEL_CLASSIFIER", "")
    risk_model: str = os.getenv("BEDROCK_MODEL_RISK", "")
    report_model: str = os.getenv("BEDROCK_MODEL_REPORT", "")
    mock_llm: bool = _bool("FINGUARD_MOCK_LLM", True)
    max_concurrency: int = int(os.getenv("FINGUARD_MAX_CONCURRENCY", "5"))
    policy_path: Path = Path(os.getenv("FINGUARD_POLICY_PATH", "data/KS_POLITICA_INTERNA.pdf"))
    dataset_path: Path = Path(os.getenv("FINGUARD_DATASET_PATH", "data/dataset.csv"))
    output_dir: Path = Path("output")


settings = Settings()
