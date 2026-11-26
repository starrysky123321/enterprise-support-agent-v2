"""Frozen confirmation cases, authored after the initial refusal policy was fixed.

Synthetic contrast pairs with new names and codes. No thresholds are tuned on this set.
The set was rerun after fixes to three original-set question parsing regressions;
it is a narrow confirmation suite, not a blind external benchmark.
They check entity/attribute confusion, not broad linguistic or real-world generalisation.
"""
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent / "datasets"


def main():
    documents, cases = [], []

    def doc(name, texts):
        documents.append({
            "id": name, "product_name": "Nebula Gateway", "product_version": "3.2",
            "document_type": "manual", "visibility": "workspace", "department": "payments",
            "workspace": "nebula-support", "deleted": False,
            "chunks": [{"id": f"chunk-{i}", "text": t} for i, t in enumerate(texts, 1)],
        })

    def case(question, name=None, chunk=None, quote=None):
        cases.append({
            "id": f"validation-{len(cases)+1:03d}",
            "category": "validation-answerable" if name else "validation-unanswerable",
            "question": question, "actor": "engineer", "version": "3.2",
            "single_doc_id": None, "answerable": name is not None,
            "evidence": [{"doc_id": name, "chunk_id": chunk, "quote": quote}] if name else [],
            "forbidden_doc_ids": [], "expect_access_denial": False,
        })

    for i, service in enumerate(["beacon", "relay", "silo", "bridge", "courier", "sentinel"]):
        name = f"validation-svc-{service}"
        timeout, retries = 31 + 9 * i, 2 + i
        doc(name, [
            f"The {service} service uses an idle timeout of {timeout} seconds.",
            f"The {service} service retries failed requests {retries} times.",
        ])
        case(f"What is the idle timeout of the {service} service?", name, "chunk-1", f"{timeout} seconds")
        case(f"How many times does the {service} service retry a failed request?", name, "chunk-2", f"{retries} times")
        case(f"Which port does the {service} service listen on?")
        case(f"What is the maximum number of pending jobs for the {service} service?")
        case(f"How many hours of audit history can the {service} service display?")
        case(f"What is the idle timeout of the unknown{service} service?")

    meanings = ["the journal is read-only", "the peer clock is ahead", "the route catalog is empty",
                "the spool volume is full", "a lease cannot be renewed", "the signing chain is incomplete"]
    for i, meaning in enumerate(meanings):
        code, name = f"OPS-{8601 + i * 7}", f"validation-code-{i}"
        doc(name, [f"{code} means {meaning}."])
        case(f"What does {code} mean?", name, "chunk-1", meaning)
        case(f"What does {code}0 mean?")

    for kind, rows in [("corpus", documents), ("eval", cases)]:
        (DATA / f"support-{kind}-validation.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8",
        )
    print(f"{len(documents)} documents; {len(cases)} cases; {sum(c['answerable'] for c in cases)} answerable")


if __name__ == "__main__":
    main()
