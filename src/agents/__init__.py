from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.agents.ask_pipeline import (
        AgentAskPipeline,
        AgentAskPipelineResult,
        DocumentNotFoundError,
    )
from src.agents.service import AgentCitation, AgentResult, AgentService
from src.agents.query_refinement import QueryRefinementResult, QueryRefinementService

__all__ = [
    "AgentAskPipeline",
    "AgentAskPipelineResult",
    "AgentCitation",
    "DocumentNotFoundError",
    "AgentResult",
    "AgentService",
    "QueryRefinementResult",
    "QueryRefinementService",
]


def __getattr__(name: str) -> Any:
    if name in {"AgentAskPipeline", "AgentAskPipelineResult", "DocumentNotFoundError"}:
        from src.agents import ask_pipeline

        return getattr(ask_pipeline, name)
    raise AttributeError(name)
