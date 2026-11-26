import asyncio

import pytest

from src.agents.service import AgentService, NO_ANSWER_FALLBACK
from src.infrastructure.llm.local_llm import LocalExtractiveLLM
from src.rag.evidence import EvidenceText, select_local_evidence
from src.rag.models import RetrievedChunk
from src.tools import ToolRegistry
from src.tools.retriever_tool import RetrieverTool


@pytest.mark.parametrize(
    ("question", "context", "expected"),
    [
        (
            "What does CFG-3207 mean?",
            EvidenceText("CFG-3207 indicates that the effective keepalive interval differs from the policy."),
            True,
        ),
        (
            "What is the default request timeout in 3.2?",
            EvidenceText("Check the default request timeout. The canary waits 90 seconds.", "3.2"),
            False,
        ),
        (
            "What is the default request timeout in 3.2?",
            EvidenceText("In 3.1, the default request timeout was 60 seconds.", "3.1"),
            False,
        ),
        (
            "What is the default request timeout in 3.2?",
            EvidenceText("In 3.1, the default request timeout was 60 seconds.", "3.2"),
            False,
        ),
        (
            "3.2版本的默认连接超时时间是多少？",
            EvidenceText("默认连接超时为 2.5 秒。", "3.2"),
            True,
        ),
        (
            "What is the default request timeout in 3.2?",
            EvidenceText("The default request timeout is 2.5 seconds.", "3.2"),
            True,
        ),
        (
            "What is the date of the certificate rotation?",
            EvidenceText("The certificate rotation is scheduled for 2026-12-02."),
            True,
        ),
        (
            "Which shell command checks the gateway status?",
            EvidenceText("To check gateway status, run `gateway status`."),
            True,
        ),
        (
            "What is the PostgreSQL table name for delivery jobs?",
            EvidenceText("For delivery jobs, the PostgreSQL table is `delivery_jobs`."),
            True,
        ),
    ],
)
def test_local_evidence_requires_the_requested_value(question, context, expected):
    assert bool(select_local_evidence(question, [context], limit=3)) is expected


def test_local_evidence_extracts_explicit_fact_beyond_initial_excerpt():
    details = "Refer to diagnostics for the network configuration. " * 10
    evidence = EvidenceText(f"{details}The default request timeout is 2.5 seconds.", "3.2")
    assert select_local_evidence(
        "What is the default request timeout for 3.2?", [evidence], limit=3,
    ) == [(0, "The default request timeout is 2.5 seconds.")]


@pytest.mark.parametrize(
    ("question", "text"),
    [
        ("How many retries does the mailer service use?", "The mailer service accepts 5 MB payloads."),
        ("What does CFG-5120 mean?", "CFG-51200 means the certificate is expiring."),
        ("What caused INC-9999?", "The root cause of INC-99990 was a disk failure."),
        ("What is the idle timeout of the mailer service?", "The search service idle timeout is 30 seconds."),
        ("What is the maximum number of routes per gateway?", "Each tenant may create 40 custom routes."),
        ("What is the encryption algorithm of the vault service?", "The vault service accepts 14 MB payloads."),
    ],
)
def test_local_evidence_does_not_confuse_entities_or_attributes(question, text):
    assert select_local_evidence(question, [EvidenceText(text, semantic_score=0.99)], limit=3) == []


def test_dense_similarity_cannot_be_borrowed_from_another_candidate():
    candidates = [
        EvidenceText("The mailer service retry policy is configurable.", semantic_score=0.99),
        EvidenceText("The search service retries failed requests 7 times.", semantic_score=0.4),
    ]
    assert select_local_evidence("How many retries does the mailer service use?", candidates, limit=3) == []


def test_supported_value_is_selected_after_a_distractor():
    candidates = [
        EvidenceText("The mailer service accepts payloads up to 5 MB.", semantic_score=0.9),
        EvidenceText("The mailer service retries failed requests 3 times.", semantic_score=0.7),
    ]
    assert select_local_evidence("How many retries does the mailer service use?", candidates, limit=3) == [
        (1, candidates[1].text),
    ]


def test_an_unsupported_clause_cannot_borrow_a_supported_clause():
    candidates = [EvidenceText("The mailer service retries failed requests 3 times.")]
    assert select_local_evidence(
        "How many retries does the mailer service use and what is its encryption algorithm?",
        candidates, limit=3,
    ) == []


@pytest.mark.parametrize(
    ("question", "text"),
    [
        ("What kind of certificate is missing when a node returns TLS-3210?",
         "TLS-3210 means the certificate chain is missing an intermediate certificate."),
        ("What status code does E_RATE_LIMIT return?", "E_RATE_LIMIT returns HTTP 429."),
        ("Which value must be reused if a failed job is replayed?",
         "For a failed job, reuse the original idempotency key on each replay."),
    ],
)
def test_question_scaffolding_is_not_mistaken_for_a_requested_attribute(question, text):
    assert select_local_evidence(question, [EvidenceText(text)], limit=3)


def test_ordinary_local_rag_refuses_when_related_sop_has_no_value():
    class RetrievedSOP:
        async def retrieve(self, **kwargs):
            return [RetrievedChunk(
                doc_id="sop", chunk_id="c1", source="SOP",
                text="Check the default request timeout in the configuration.", score=0.9,
                product_version="3.2",
            )]

    registry = ToolRegistry([RetrieverTool(retrieval_service=RetrievedSOP(), default_top_k=3)])
    agent = AgentService(llm=LocalExtractiveLLM(), registry=registry)
    result = asyncio.run(agent.run(
        question="What is the default request timeout for 3.2?", doc_id="sop",
    ))
    assert result.answer == NO_ANSWER_FALLBACK
    assert result.citations == []
