"""Screenshot capture for web deliverables, via Playwright headless chromium.

Hardened for slow/heavy real apps: navigate on ``domcontentloaded`` (don't block
on a slow ``load`` that waits for a stuck subresource), settle briefly, then
screenshot the viewport with a generous timeout and one retry. A page that loads
but can't be screenshotted returns an empty ``screenshot_path`` + ``error`` so the
verifier fails it explicitly rather than hanging.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Capture:
    url: str
    screenshot_path: str   # "" if no screenshot was captured
    loaded: bool           # navigation reached domcontentloaded
    title: str = ""
    error: str = ""

    @property
    def captured(self) -> bool:
        return bool(self.screenshot_path)


def _safe_name(url: str) -> str:
    keep = [c if c.isalnum() else "-" for c in url]
    return ("".join(keep).strip("-")[:80]) or "page"


def capture(
    url: str,
    out_dir: str | Path,
    *,
    full_page: bool = False,
    settle_ms: int = 700,
    nav_timeout_ms: int = 60000,
    screenshot_timeout_ms: int = 60000,
    viewport: tuple[int, int] = (1280, 900),
    retries: int = 1,
) -> Capture:
    """Load ``url`` headless and screenshot it under ``out_dir`` (robust to slow pages)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    shot = out / f"{_safe_name(url)}.png"

    from playwright.sync_api import sync_playwright  # optional dep, imported lazily

    title = ""
    error = ""
    loaded = False
    captured = False
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
            try:
                # domcontentloaded, not "load": a stuck subresource (favicon, long
                # poll) must not stall us, and the slow first search has rendered HTML.
                page.goto(url, timeout=nav_timeout_ms, wait_until="domcontentloaded")
                loaded = True
                try:
                    title = page.title()
                except Exception:
                    title = ""
            except Exception as exc:
                error = f"nav: {type(exc).__name__}: {exc}"

            page.wait_for_timeout(settle_ms)

            clip = None if full_page else {"x": 0, "y": 0, "width": viewport[0], "height": viewport[1]}
            for _ in range(retries + 1):
                try:
                    page.screenshot(
                        path=str(shot), full_page=full_page, clip=clip,
                        animations="disabled", caret="initial", timeout=screenshot_timeout_ms,
                    )
                    captured = True
                    error = ""
                    break
                except Exception as exc:
                    error = f"screenshot: {type(exc).__name__}: {exc}"
                    page.wait_for_timeout(800)
        finally:
            browser.close()

    return Capture(
        url=url,
        screenshot_path=str(shot) if captured else "",
        loaded=loaded,
        title=title,
        error=error,
    )
