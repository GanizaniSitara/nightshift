"""Screenshot capture for web deliverables, via Playwright headless chromium."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Capture:
    url: str
    screenshot_path: str
    loaded: bool
    title: str = ""
    error: str = ""


def _safe_name(url: str) -> str:
    keep = [c if c.isalnum() else "-" for c in url]
    name = "".join(keep).strip("-")
    return (name[:80] or "page")


def capture(
    url: str,
    out_dir: str | Path,
    *,
    full_page: bool = False,
    wait_ms: int = 800,
    timeout_ms: int = 30000,
    viewport: tuple[int, int] = (1280, 900),
) -> Capture:
    """Load ``url`` in headless chromium and save a screenshot under ``out_dir``.

    Returns a ``Capture`` with ``loaded`` False (and ``error`` set) if the page
    could not be reached — the verifier turns that into a FAIL rather than raising.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    shot = out / f"{_safe_name(url)}.png"

    from playwright.sync_api import sync_playwright  # imported lazily; optional dep

    title = ""
    error = ""
    loaded = False
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
            try:
                page.goto(url, timeout=timeout_ms, wait_until="load")
                loaded = True
                title = page.title()
            except Exception as exc:  # navigation failure — still screenshot what's there
                error = f"{type(exc).__name__}: {exc}"
            if wait_ms:
                time.sleep(wait_ms / 1000)
            if full_page:
                page.screenshot(
                    path=str(shot), full_page=True, animations="disabled", timeout=timeout_ms
                )
            else:
                # Clip to the viewport: avoids the full-page measurement / stability wait
                # that hangs on some pages even after fonts/animations settle.
                page.screenshot(
                    path=str(shot),
                    clip={"x": 0, "y": 0, "width": viewport[0], "height": viewport[1]},
                    animations="disabled",
                    timeout=timeout_ms,
                )
        finally:
            browser.close()

    return Capture(url=url, screenshot_path=str(shot), loaded=loaded, title=title, error=error)
