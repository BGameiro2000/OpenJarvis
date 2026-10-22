"""Browser smoke test of the HUD in demo mode (Playwright, Chromium).

Usage: python ui_smoke.py http://127.0.0.1:8080 OUT_DIR [chromium path]
Checks: no CSP violation or page error, a demo turn renders Root cutting in,
note text with HTML stays text, reduced motion removes animation, phone width
has no horizontal scroll. Saves screenshots to OUT_DIR.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else ".")
EXE = sys.argv[3] if len(sys.argv) > 3 else None
OUT.mkdir(parents=True, exist_ok=True)
XSS = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>'


def turn(page, text: str, n: int) -> None:
    page.fill("input[aria-label=Message]", text)
    page.click("button[type=submit]")
    # Poll from Python: the CSP forbids the eval behind wait_for_function.
    for _ in range(200):
        facts = page.locator(".line .facts").all_inner_texts()
        if sum(f.startswith("end") for f in facts) >= n:
            return
        time.sleep(0.1)
    raise TimeoutError(f"turn {n} did not end")


def main() -> int:
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EXE) if EXE else p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        page.on("console", lambda m: m.type == "error" and problems.append(f"console: {m.text}"))
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        page.goto(URL)
        page.wait_for_selector(".conn.live", timeout=10000)
        page.screenshot(path=str(OUT / "hud-idle.png"))

        turn(page, "Should I get the A2000 now, or do the RAM upgrade first?", 1)
        tags = page.locator(".row .tag").all_inner_texts()
        if "ROOT ▸ CUT IN" not in tags:
            problems.append(f"no interjection in feed: {tags}")
        if not page.locator(".dropped").count():
            problems.append("dropped sentences not shown")
        page.screenshot(path=str(OUT / "hud-interject.png"))

        turn(page, "How should I structure the paper?", 2)
        page.screenshot(path=str(OUT / "hud-build.png"))

        turn(page, XSS, 3)
        if page.evaluate("window.__pwned"):
            problems.append("HTML in text was executed")
        if XSS not in page.locator("h1").inner_text():
            problems.append("HTML in text was not shown as text")

        # Memory: hybrid search, tags, graph, inspector
        page.goto(URL + "/#/memory")
        page.fill("input[aria-label='Search memory']", "RAM upgrade")
        page.click("form.bar button[type=submit]")
        page.wait_for_selector(".hit", timeout=10000)
        tags = set(page.locator(".hit .tag").all_inner_texts())
        if "BOTH" not in tags:
            problems.append(f"no BOTH tag in hybrid search: {tags}")
        page.wait_for_selector(".graph canvas", timeout=10000)
        page.wait_for_selector(".insp h2", timeout=10000)
        page.screenshot(path=str(OUT / "hud-memory.png"))
        page.fill("input[aria-label='Search memory']", "note tries to run code")
        page.click("form.bar button[type=submit]")
        page.wait_for_selector(".insp .body", timeout=10000)
        time.sleep(0.5)

        # Intake: dry run, apply, escalations, HTML stays text
        page.on("dialog", lambda d: d.accept())
        page.goto(URL + "/#/intake")
        page.wait_for_selector(".escal .cards", timeout=10000)
        page.get_by_role("button", name="Dry run now").click()
        page.wait_for_selector(".pipe .row:not(.hdr)", timeout=10000)
        page.screenshot(path=str(OUT / "hud-intake.png"), full_page=True)
        if "<script>" not in page.locator(".escal").inner_text():
            problems.append("note with markup not shown as text on the intake board")
        page.get_by_role("button", name="Apply", exact=True).click()
        page.wait_for_selector("text=Applied", timeout=10000)

        # Council: replay and a new run
        page.goto(URL + "/#/council/2026-10-21-0381")
        page.wait_for_selector(".dec", timeout=10000)
        if page.locator(".dec").inner_text() != "WAIT":
            problems.append("council replay verdict is not WAIT")
        if page.locator(".god.called").count() != 3:
            problems.append(f"expected 3 called cards, got {page.locator('.god.called').count()}")
        time.sleep(3)
        page.screenshot(path=str(OUT / "hud-council.png"), full_page=True)

        page.goto(URL + "/")
        page.wait_for_selector(".conn.live", timeout=10000)
        page.emulate_media(reduced_motion="reduce")
        anim = page.evaluate(
            "getComputedStyle(document.querySelector('.box .c')).animationName"
        )
        if anim != "none":
            problems.append(f"reduced motion still animates: {anim}")

        phone = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        phone.goto(URL)
        phone.wait_for_selector(".conn.live", timeout=10000)
        overflow = phone.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        if overflow > 0:
            problems.append(f"phone view scrolls sideways by {overflow}px")
        phone.screenshot(path=str(OUT / "hud-phone.png"), full_page=False)
        browser.close()

    for line in problems:
        print("FAIL", line)
    print("ui smoke:", "ok" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
