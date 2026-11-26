"""Run identical, reviewed cases against a fixed Git revision and the working source.

The baseline uses a temporary copy of the revision's src/ and today's evaluator/datasets.
It never replaces files in the working checkout. Both runs use the same models and corpus.
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = "2a8bea70f68cfaed6532a43b81b8ff9befee9374"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-revision", default=REFERENCE)
    parser.add_argument("--embedding-model", required=True)
    parser.add_argument("--reranker-model")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "eval" / "reports")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    flags = ["--hard", "--holdout", "--validation", "--review-labels",
             "--embedding-model", args.embedding_model]
    if args.reranker_model:
        flags += ["--reranker-model", args.reranker_model]
    archive = subprocess.check_output(
        ["git", "archive", args.reference_revision, "src"], cwd=ROOT,
    )
    with tempfile.TemporaryDirectory(prefix="support-refusal-reference-") as directory:
        reference = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(reference, filter="data")
        shutil.copytree(ROOT / "eval" / "datasets", reference / "eval" / "datasets")
        for name in ["run_local_evaluation.py", "reviewed_labels.json"]:
            shutil.copy2(ROOT / "eval" / name, reference / "eval" / name)
        subprocess.run(
            [sys.executable, str(reference / "eval" / "run_local_evaluation.py"),
             *flags, "--output", str(output / "refusal-confirm-before.json")],
            cwd=reference, check=True,
        )
    subprocess.run(
        [sys.executable, str(ROOT / "eval" / "run_local_evaluation.py"),
         *flags, "--output", str(output / "refusal-confirm-after.json")],
        cwd=ROOT, check=True,
    )


if __name__ == "__main__":
    main()
