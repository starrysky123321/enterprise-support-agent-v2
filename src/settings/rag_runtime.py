from functools import lru_cache
from pathlib import Path

from pydantic_settings import SettingsConfigDict

from src.settings.agent import AgentSettings
from src.settings.ai import AISettings
from src.settings.rag import RAGSettings

BASE_DIR = Path(__file__).resolve().parents[2]


class RagRuntimeSettings(AISettings, AgentSettings, RAGSettings):
    model_config = SettingsConfigDict(extra="ignore")


@lru_cache
def get_rag_settings() -> RagRuntimeSettings:
    return RagRuntimeSettings()
