"""Central settings. Every value can be overridden with an environment variable or a .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str, default: str) -> tuple[str, ...]:
    raw = os.environ.get(name, default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    # --- Credentials (never hard-code these) ---
    pinecone_api_key: str = field(default_factory=lambda: _str("PINECONE_API_KEY"))
    groq_api_key: str = field(default_factory=lambda: _str("GROQ_API_KEY"))

    # --- Vector store ---
    index_name: str = field(default_factory=lambda: _str("INDEX_NAME", "company-policies"))
    pinecone_cloud: str = field(default_factory=lambda: _str("PINECONE_CLOUD", "aws"))
    pinecone_region: str = field(default_factory=lambda: _str("PINECONE_REGION", "us-east-1"))

    # --- Models ---
    embed_model: str = field(default_factory=lambda: _str("EMBED_MODEL", "BAAI/bge-small-en-v1.5"))
    llm_model: str = field(default_factory=lambda: _str("LLM_MODEL", "openai/gpt-oss-20b"))
    llm_base_url: str = field(default_factory=lambda: _str("LLM_BASE_URL", "https://api.groq.com/openai/v1"))
    llm_temperature: float = field(default_factory=lambda: _float("LLM_TEMPERATURE", 0.1))
    safety_model: str = field(default_factory=lambda: _str("SAFETY_MODEL", "openai/gpt-oss-safeguard-20b"))
    enable_llm_moderation: bool = field(default_factory=lambda: _bool("ENABLE_LLM_MODERATION", True))

    # --- Chunking ---
    max_chunk_words: int = field(default_factory=lambda: _int("MAX_CHUNK_WORDS", 200))

    # --- Retrieval & answering ---
    top_k: int = field(default_factory=lambda: _int("TOP_K", 8))
    candidate_k: int = field(default_factory=lambda: _int("CANDIDATE_K", 20))
    min_relevance: float = field(default_factory=lambda: _float("MIN_RELEVANCE", 0.55))
    history_turns: int = field(default_factory=lambda: _int("HISTORY_TURNS", 6))

    # --- Safety & security limits ---
    max_question_chars: int = field(default_factory=lambda: _int("MAX_QUESTION_CHARS", 1000))
    rate_limit_per_minute: int = field(default_factory=lambda: _int("RATE_LIMIT_PER_MINUTE", 10))
    app_password: str = field(default_factory=lambda: _str("APP_PASSWORD"))
    admin_password: str = field(default_factory=lambda: _str("ADMIN_PASSWORD"))
    maintenance_mode: bool = field(default_factory=lambda: _bool("MAINTENANCE_MODE", False))

    # --- Privacy ---
    # PII removed from documents before they are embedded and stored.
    document_pii_types: tuple[str, ...] = field(
        default_factory=lambda: _list("DOCUMENT_PII_TYPES", "ssn,credit_card,iban"))
    # PII removed from user questions before they reach the LLM or the logs.
    question_pii_types: tuple[str, ...] = field(
        default_factory=lambda: _list("QUESTION_PII_TYPES", "ssn,credit_card,iban,email,phone"))
    # PII removed from answers before they are shown.
    answer_pii_types: tuple[str, ...] = field(
        default_factory=lambda: _list("ANSWER_PII_TYPES", "ssn,credit_card,iban"))

    # --- Accountability ---
    log_dir: str = field(default_factory=lambda: _str("LOG_DIR", "logs"))
    log_retention_days: int = field(default_factory=lambda: _int("LOG_RETENTION_DAYS", 90))
    escalation_contact: str = field(
        default_factory=lambda: _str("ESCALATION_CONTACT", "People Operations or the relevant policy owner"))

    # --- Data ---
    data_dir: str = field(default_factory=lambda: _str("DATA_DIR", "data"))


def get_settings() -> Settings:
    """Read settings fresh from the environment."""
    return Settings()
