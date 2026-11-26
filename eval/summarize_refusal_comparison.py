"""Produce Wilson intervals and paired bootstrap comparisons from saved runs."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SEED = 20261121
RESAMPLES = 10_000


def interval(values):
    a = np.asarray(values, dtype=float)
    if not len(a):
        raise ValueError("Cannot estimate an empty group")
    rng = np.random.default_rng(SEED)
    means = a[rng.integers(0, len(a), size=(RESAMPLES, len(a)))].mean(axis=1)
    return {"n": len(a), "mean": float(a.mean()), "ci95": np.quantile(means, [0.025, 0.975]).tolist()}


def proportion(values):
    n, k = len(values), sum(values)
    if not n:
        return None
    p, z = k/n, 1.959963984540054
    center = (p + z*z/(2*n)) / (1+z*z/n)
    half = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1+z*z/n)
    return {"successes": k, "n": n, "rate": p, "ci95": [center-half, center+half]}


def split(case_id):
    if case_id.startswith("validation-"):
        return "confirmation"
    if case_id.startswith("holdout-"):
        return "previous_holdout_development"
    if case_id.startswith("hard-"):
        return "hard_development"
    return "original"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, default=ROOT / "eval" / "reports")
    args = parser.parse_args()
    reports = {
        name: json.loads((args.reports / f"refusal-confirm-{name}.json").read_text())
        for name in ("before", "after")
    }
    before, after = reports["before"], reports["after"]
    for key in ["datasets_sha256", "label_reviews", "embedding_model", "reranker_model", "generation_model"]:
        if before["parameters"][key] != after["parameters"][key]:
            raise ValueError(f"Mismatched evaluation conditions: {key}")
    result = {"bootstrap_seed": SEED, "resamples": RESAMPLES,
              "binomial_interval": "Wilson score", "groups": {}, "retrieval": {}, "paired": {},
              "statistics_numpy": np.__version__,
              "provenance": {
                  stage: {"generated_at": report["generated_at"], "runtime": report["runtime"],
                          "parameters": report["parameters"],
                          "report_sha256": sha256((args.reports / f"refusal-confirm-{stage}.json").read_bytes()).hexdigest()}
                  for stage, report in reports.items()
              }}
    result["retrieval_run_differences"] = {}
    for mode, payload in after["retrieval"].items():
        reference = {row["id"]: row for row in before["retrieval"][mode]["cases"]}
        result["retrieval_run_differences"][mode] = {
            "ranking_changed_ids": [r["id"] for r in payload["cases"]
                                    if r["chunk_ids"] != reference[r["id"]]["chunk_ids"]],
            "metric_changed_ids": [r["id"] for r in payload["cases"]
                                   if any(r.get(m) != reference[r["id"]].get(m)
                                          for m in ["recall_at_5", "mrr_at_5"])],
        }
    for group in ["all", "original", "hard_development", "previous_holdout_development", "confirmation"]:
        result["groups"][group] = {}
        for stage, report in reports.items():
            rows = [row for mode in ["agentic_rag_paired", "agentic_rag_multi_document"]
                    for row in report["api"][mode]["cases"]
                    if group == "all" or split(row["id"]) == group]
            # Scope-empty refusals belong to access control, not semantic answerability.
            result["groups"][group][stage] = {
                "correct_refusal": proportion([int(r["correct_refusal"]) for r in rows
                                                if "correct_refusal" in r and r["category"] != "permission"]),
                "false_refusal": proportion([int(r["false_refusal"]) for r in rows if "false_refusal" in r]),
                "fact_coverage": interval([r["reference_fact_coverage"] for r in rows
                                            if "reference_fact_coverage" in r]),
                "scope_violations": sum(bool(r["out_of_scope_doc_ids"]) for r in rows),
            }
    for mode, payload in after["retrieval"].items():
        result["retrieval"][mode] = {
            metric: interval([r[metric] for r in payload["cases"] if metric in r])
            for metric in ["recall_at_5", "mrr_at_5"]
        }
    for lhs, rhs in [("hybrid_rrf", "bm25_only"), ("hybrid_rrf", "dense_only"),
                     ("hybrid_plus_reranker", "hybrid_rrf")]:
        left = {r["id"]: r for r in after["retrieval"][lhs]["cases"]}
        right = {r["id"]: r for r in after["retrieval"][rhs]["cases"]}
        if left.keys() != right.keys():
            raise ValueError("Pairing requires identical case IDs")
        result["paired"][f"{lhs}-minus-{rhs}"] = {}
        for metric in ["recall_at_5", "mrr_at_5"]:
            differences = [r[metric]-right[key][metric] for key, r in left.items() if metric in r]
            result["paired"][f"{lhs}-minus-{rhs}"][metric] = {
                **interval(differences), "wins": sum(d > 0 for d in differences),
                "losses": sum(d < 0 for d in differences), "ties": sum(d == 0 for d in differences),
            }
    rows = {stage: {r["id"]: r for mode in ["agentic_rag_paired", "agentic_rag_multi_document"]
                   for r in report["api"][mode]["cases"]} for stage, report in reports.items()}
    if rows["before"].keys() != rows["after"].keys():
        raise ValueError("Before/after runs must cover the same questions")
    for metric in ["correct_refusal", "false_refusal", "reference_fact_coverage"]:
        diffs = [r[metric] - rows["before"][key][metric] for key, r in rows["after"].items()
                 if metric in r and r["category"] != "permission"]
        result["paired"][f"after-minus-before-{metric}"] = interval(diffs)
    result["remaining_failures"] = [
        {"id": key, "category": row["category"], "kind": "false_refusal" if row.get("false_refusal") else "missed_refusal"}
        for key, row in rows["after"].items()
        if row.get("false_refusal") or row.get("correct_refusal") is False
    ]
    (args.reports / "refusal-comparison.json").write_text(json.dumps(result, indent=2)+"\n")
    (ROOT / "docs" / "refusal-evaluation.json").write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
