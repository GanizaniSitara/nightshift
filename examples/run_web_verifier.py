"""Run the web Verifier against a URL with a rubric file. Generic reference runner.

Usage:
  python examples/run_web_verifier.py <url> <rubric.md> [--criteria "..."] [--model sonnet]

Project-specific targets/rubrics should live in your private plugins dir, not here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nightshift.verifiers.base import Increment  # noqa: E402
from nightshift.verifiers.web import WebVerifier  # noqa: E402


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("rubric")
    ap.add_argument("--criteria", action="append", default=[])
    ap.add_argument("--evidence", default="state/evidence")
    ap.add_argument("--model", default="sonnet")
    args = ap.parse_args()

    increment = Increment(
        id="adhoc",
        summary="adhoc web check",
        deliverable_type="web",
        target=args.url,
        rubric_path=args.rubric,
        acceptance_criteria=args.criteria,
    )
    result = WebVerifier().verify(
        increment, config={"evidence_dir": args.evidence, "eval_model": args.model}
    )

    print("VERDICT:", result.verdict.value)
    print("BUILT:", result.built, "| screenshot:", result.screenshots)
    print("NOTES:", result.notes)
    for f in result.vision_findings:
        print(f"  [{f.verdict.value}] {f.rubric_item} :: {f.notes}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
