"""Capture README screenshots from the running app (run after record_demo.py so the database holds real results)."""
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
    page.add_init_script("try { localStorage.setItem('ah.tenant','acme'); localStorage.setItem('ah.user','maya') } catch (e) {}")
    page.goto("http://localhost:8000")
    page.wait_for_selector("[data-testid=agent-select]")
    page.select_option("[data-testid=agent-select]", index=0)
    page.wait_for_selector("[data-testid=kpis]")
    page.wait_for_timeout(1200)
    page.screenshot(path=OUT / "01-dashboard.png")

    page.click("[data-nav=traces]")
    page.wait_for_selector("[data-testid=trace-run-select] option", state="attached")
    page.wait_for_timeout(800)
    page.select_option("[data-testid=trace-run-select]", index=page.locator("[data-testid=trace-run-select] option").count() - 1)
    page.wait_for_timeout(1000)
    page.click("text=Failure analysis (")
    page.wait_for_timeout(800)
    page.screenshot(path=OUT / "02-failure-analysis.png")
    page.click("text=Case results & traces")
    page.click("[data-case=IT-003]")
    page.wait_for_selector("[data-testid=trace]")
    span = page.locator(".span", has_text="Issue a temporary credential").first
    span.locator(".span-head").click()
    page.wait_for_timeout(600)
    span.scroll_into_view_if_needed()
    page.screenshot(path=OUT / "03-trace.png")

    page.click("[data-nav=improve]")
    page.wait_for_timeout(1200)
    page.screenshot(path=OUT / "04-recommendations.png")
    page.click(".tabs button:has-text('Experiments')")
    page.wait_for_selector("[data-testid=comparison]")
    page.locator(".card", has_text="Baseline vs candidate").first.scroll_into_view_if_needed()
    page.wait_for_timeout(600)
    page.screenshot(path=OUT / "05-experiment-comparison.png")

    page.click("[data-nav=regression]")
    page.wait_for_timeout(1200)
    page.screenshot(path=OUT / "06-regression-learning.png")
    page.click("[data-nav=settings]")
    page.wait_for_timeout(1000)
    page.screenshot(path=OUT / "07-llm-providers.png")
    page.click(".tabs button:has-text('Model roles')")
    page.wait_for_timeout(1000)
    page.screenshot(path=OUT / "08-model-roles.png")
    b.close()
print("saved", sorted(x.name for x in OUT.glob("*.png")))
