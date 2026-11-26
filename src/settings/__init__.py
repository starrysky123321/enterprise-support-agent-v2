from typing import TYPE_CHECKING, Any

from src.settings.rag import RAGSettings

if TYPE_CHECKING:
    from src.settings.config import Settings, settings

__all__ = ["RAGSettings", "Settings", "settings"]


def __getattr__(name: str) -> Any:
    if name in {"Settings", "settings"}:
        from src.settings import config

        return getattr(config, name)
    raise AttributeError(name)
