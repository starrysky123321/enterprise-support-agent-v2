"""Seed a dedicated workspace and exercise the Spring Boot publication API."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
import json
import math
import os
from random import Random
import re
from time import monotonic, perf_counter
from uuid import uuid4

import httpx


WORKLOADS = {
    "mixed": (("search", 80), ("status", 12), ("ask", 6), ("upload", 2)),
    "read": (("search", 90), ("status", 10)),
    "overload": (("search", 50), ("ask", 50)),
}
TOPICS = ("gateway", "webhook", "scheduler", "catalog", "billing", "identity", "reporting", "storage")


def percentile(ascending: list[float], pct: float) -> float | None:
    if not ascending:
        return None
    return round(ascending[math.ceil(pct * len(ascending)) - 1], 2)


async def run(args: argparse.Namespace, admin_token: str) -> dict:
    run_id = uuid4().hex[:12]
    workspace = args.workspace or f"load-{uuid4().hex[:12]}"
    prefix = f"/api/knowledge/workspaces/{workspace}"
    documents = [f"{prefix}/documents/sop-{i}" for i in range(args.documents)]
    questions = [
        {"query": f"What is the idle timeout for {TOPICS[i % len(TOPICS)]} cluster OPS{i:05d}?"}
        for i in range(len(documents))
    ]
    contents = []
    for i in range(len(documents)):
        topic = TOPICS[i % len(TOPICS)]
        cluster = f"{topic} cluster OPS{i:05d}"
        paragraphs = [
            f"Procedure {i + 1}.{j + 1} for {cluster}. Review {TOPICS[(i + j) % len(TOPICS)]} "
            f"dashboard {i + j + 100}, inspect alarm OPS-{i * args.paragraphs + j:06d}, and verify the "
            f"upstream connection utilization before escalating to rotation {1 + (i + j) % 7}.\n"
            for j in range(args.paragraphs)
        ]
        contents.append(f"The idle timeout for {cluster} is {30 + i % 91} seconds.\n" + "".join(paragraphs))
    limits = httpx.Limits(max_connections=args.concurrency + 4, max_keepalive_connections=args.concurrency)
    async with httpx.AsyncClient(timeout=12, limits=limits, trust_env=False) as client:
        admin = {"Authorization": f"Bearer {admin_token}"}
        first = args.url[0]
        (await client.put(first + prefix, headers=admin)).raise_for_status()
        key = (await client.post(first + prefix + "/keys", headers=admin, json={"role": "EDITOR"}))
        token = key.raise_for_status().json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        def query_path(document: str) -> str:
            return prefix if args.scope == "workspace" else document

        async def seed(i: int) -> None:
            document = documents[i]
            (await client.post(first + document + "/versions", headers=headers,
                               json={"text": contents[i]})).raise_for_status()
            deadline = monotonic() + 90
            while True:
                response = await client.get(first + document + "/versions/1", headers=headers)
                status = response.raise_for_status().json()
                if status["status"] == "INDEXED":
                    return
                if status["status"] == "FAILED" or monotonic() >= deadline:
                    raise RuntimeError(f"Could not publish seed document {document}: {status}")
                await asyncio.sleep(0.2)

        seed_started = perf_counter()
        for start in range(0, len(documents), 24):
            await asyncio.gather(*(seed(i) for i in range(start, min(start + 24, len(documents)))))
        seed_elapsed = perf_counter() - seed_started
        for url in args.url:
            probe = (await client.post(url + query_path(documents[0]) + "/search",
                                       headers=headers, json=questions[0])).raise_for_status().json()
            if not probe["result"]["chunks"]:
                raise RuntimeError("The seed workspace returned no searchable chunks")

        status_counts: dict[str, Counter[str]] = defaultdict(Counter)
        latencies: dict[str, list[float]] = defaultdict(list)
        answers: Counter[str] = Counter()
        start = perf_counter()
        deadline = start + args.seconds
        operations, weights = zip(*WORKLOADS[args.workload])

        async def drive(worker: int) -> None:
            random = Random(worker)
            sequence = worker
            while perf_counter() < deadline:
                operation = random.choices(operations, weights=weights)[0]
                i = random.randrange(len(documents))
                document = documents[i]
                url = args.url[sequence % len(args.url)]
                base = url + document
                sequence += 1
                request_start = perf_counter()
                try:
                    if operation == "status":
                        response = await client.get(base + "/versions/1", headers=headers)
                    elif operation == "upload":
                        response = await client.post(base + "/versions", headers=headers, json={
                            "text": contents[i] + f"Load sample {run_id}-{worker}-{sequence}.\n",
                        })
                    else:
                        response = await client.post(
                            url + query_path(document) + f"/{operation}", headers=headers, json=questions[i],
                            timeout=args.ask_timeout if operation == "ask" else 12,
                        )
                    status_counts[operation][str(response.status_code)] += 1
                    if response.is_success:
                        latencies[operation].append((perf_counter() - request_start) * 1000)
                        if operation == "ask":
                            payload = response.json()
                            result = payload["result"]
                            answers[result["status"]] += 1
                            if result["citations"]:
                                answers["with_citations"] += 1
                                timeout_pattern = rf"(?<!\d){30 + i % 91}[\s-]*(?:seconds?\b|秒)"
                                if re.search(timeout_pattern, result["answer"], re.IGNORECASE):
                                    answers["with_expected_timeout"] += 1
                                    if args.scope == "document" or any(
                                        source["documentId"] == f"sop-{i}" for source in payload["documents"]
                                    ):
                                        answers["with_expected_timeout_and_source"] += 1
                except httpx.HTTPError as error:
                    status_counts[operation][type(error).__name__] += 1

        await asyncio.gather(*(drive(worker) for worker in range(args.concurrency)))
        elapsed = perf_counter() - start

    total = sum(sum(counts.values()) for counts in status_counts.values())
    successes = sum(map(len, latencies.values()))
    shedding = sum(counts["503"] + counts["429"] for counts in status_counts.values())
    details = {}
    for operation, counts in sorted(status_counts.items()):
        ascending = sorted(latencies[operation])
        details[operation] = {
            "statuses": dict(counts),
            "p50_ms": percentile(ascending, .5),
            "p95_ms": percentile(ascending, .95),
            "p99_ms": percentile(ascending, .99),
        }
    return {
        "workload": args.workload, "scope": args.scope, "model": args.model_label,
        "embedding": args.embedding_label,
        "workspace": workspace, "run_id": run_id,
        "concurrency": args.concurrency, "urls": args.url,
        "seed_documents": len(documents), "seed_characters": sum(map(len, contents)),
        "seed_duration_s": round(seed_elapsed, 2), "answer_outcomes": dict(answers),
        "offer_window_s": args.seconds, "duration_s": round(elapsed, 2),
        "attempts": total, "successes": successes,
        "successful_rps": round(successes / elapsed, 1),
        "capacity_responses": shedding, "unexpected_errors": total - successes - shedding,
        "operations": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", action="append", help="API base URL. May be passed repeatedly.")
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=64)
    parser.add_argument("--documents", type=int, default=24)
    parser.add_argument("--paragraphs", type=int, default=16)
    parser.add_argument("--workload", choices=WORKLOADS, default="mixed")
    parser.add_argument("--scope", choices=("document", "workspace"), default="document")
    parser.add_argument("--workspace", help="Re-seed a prior load-test workspace using identical corpus settings")
    parser.add_argument("--model-label", default="unspecified")
    parser.add_argument("--embedding-label", default="unspecified")
    parser.add_argument("--ask-timeout", type=float, default=65)
    args = parser.parse_args()
    args.url = [url.rstrip("/") for url in args.url or ["http://127.0.0.1:8081"]]
    if min(args.seconds, args.concurrency, args.documents, args.paragraphs, args.ask_timeout) < 1:
        parser.error("duration, concurrency, documents and paragraphs must be positive")
    if args.workspace and not re.fullmatch(r"load-[a-f0-9]{12}", args.workspace):
        parser.error("workspace must be a load-test workspace ID generated by this script")
    admin_token = os.environ.get("KNOWLEDGE_API_TOKEN")
    if not admin_token:
        parser.error("KNOWLEDGE_API_TOKEN is required to seed the load-test workspace")
    report = asyncio.run(run(args, admin_token))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["unexpected_errors"] or not report["successes"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
