"""Web-app Verifier (reference implementation).

Load a URL, screenshot it (Playwright), and judge the screenshot against a rubric
by shelling out to a coding-agent CLI. This is the generic, public reference
Verifier; project-specific targets and rubrics live in private plugins/config.

The increment supplies:
- ``target``      the URL to load
- ``rubric_path`` a rubric file (one check per line) for the vision pass
- ``acceptance_criteria`` extra checks folded into the prompt
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import registry
from .base import Increment, Verdict, Verifier, VerificationResult
from .capture import capture
from .evaluator import evaluate_screenshot


class WebVerifier(Verifier):
    deliverable_type = "web"

    def verify(self, increment: Increment, *, config: dict[str, Any]) -> VerificationResult:
        cfg = config or {}
        out_dir = cfg.get("evidence_dir", "state/evidence")
        tool = cfg.get("eval_tool", "claude")
        model = cfg.get("eval_model", "sonnet")
        timeout = int(cfg.get("eval_timeout", 300))

        if not increment.target:
            return VerificationResult(
                deliverable_type=self.deliverable_type,
                verdict=Verdict.FAIL,
                notes="no target URL on increment",
            )

        shot = capture(
            increment.target, out_dir,
            full_page=bool(cfg.get("web_full_page", True)),  # see below-the-fold results
            nav_timeout_ms=int(cfg.get("nav_timeout_ms", 60000)),
            screenshot_timeout_ms=int(cfg.get("screenshot_timeout_ms", 60000)),
            settle_ms=int(cfg.get("settle_ms", 700)),
        )
        result = VerificationResult(
            deliverable_type=self.deliverable_type,
            verdict=Verdict.FAIL,
            built=shot.captured,
            screenshots=[shot.screenshot_path] if shot.captured else [],
            evidence_paths=[shot.screenshot_path] if shot.captured else [],
        )
        # Gate on HAVING a screenshot, not on navigation: slow apps can miss
        # domcontentloaded yet still render a judgeable page.
        if not shot.captured:
            loaded = "loaded but " if shot.loaded else "did not load; "
            result.notes = f"no screenshot ({loaded}{shot.error})"
            return result

        rubric_text = ""
        if increment.rubric_path and Path(increment.rubric_path).is_file():
            rubric_text = Path(increment.rubric_path).read_text(encoding="utf-8")

        verdict, findings, raw = evaluate_screenshot(
            shot.screenshot_path,
            rubric_text,
            increment.acceptance_criteria,
            tool=tool,
            model=model,
            timeout=timeout,
        )
        result.verdict = verdict
        result.vision_findings = findings
        result.notes = f"title={shot.title!r}; {len(findings)} rubric findings"
        return result


web_verifier = registry.register(WebVerifier())
