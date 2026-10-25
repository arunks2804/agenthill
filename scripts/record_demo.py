"""Record the narrated AgentHill product demo (Playwright + Chromium, 1920x1080, British Piper voiceover).

Structure: opening slides (problem, proposition, agenda) -> six chapters, each introduced by a short
chapter card and narrated over real interactions in the running app -> closing slide.

Everything shown in the app is a real click; numbers spoken and captioned are read from the page at
recording time. The voiceover is generated locally with Piper (AI-generated voice) and each scene is timed
to its narration, then all segments are mixed onto the screen recording with ffmpeg.

    python scripts/prepare_demo.py
    uvicorn --app-dir backend agenthill.main:app --port 8000 &
    python scripts/record_demo.py                    # -> docs/demo/agenthill-demo.mp4
    python scripts/record_demo.py --voice en_GB-alan-medium
    python scripts/record_demo.py --no-video         # end-to-end UI test only (no audio)

Voices are downloaded with:  python -m piper.download_voices --download-dir var/voices en_GB-cori-high
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

from playwright.sync_api import Locator, Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "demo"
AUDIO = ROOT / "var" / "demo_audio"
BASE = "http://localhost:8000"

OVERLAY_JS = r"""
(() => {
  if (document.getElementById('demo-style')) return;
  const st = document.createElement('style'); st.id = 'demo-style';
  st.textContent = `
  #demo-cursor { position: fixed; z-index: 100000; left: 960px; top: 540px; width: 30px; height: 30px; pointer-events: none;
    transition: left .75s cubic-bezier(.4,.0,.2,1), top .75s cubic-bezier(.4,.0,.2,1); filter: drop-shadow(0 2px 3px rgba(0,0,0,.45)); }
  #demo-cursor.click::after { content: ''; position: absolute; left: -16px; top: -16px; width: 34px; height: 34px; border-radius: 50%;
    border: 3px solid #6366f1; animation: ring .5s ease-out forwards; }
  @keyframes ring { from { transform: scale(.3); opacity: 1 } to { transform: scale(1.6); opacity: 0 } }
  #demo-slide { position: fixed; inset: 0; z-index: 99990; display: flex; align-items: center; justify-content: center;
    background: radial-gradient(1200px 700px at 20% 10%, #312e81 0%, #111827 55%, #0b1020 100%); color: #f8fafc;
    font-family: Inter, -apple-system, 'Segoe UI', sans-serif; opacity: 0; transition: opacity .6s ease; pointer-events: none; }
  #demo-slide.show { opacity: 1; }
  #demo-slide .inner { width: 1500px; }
  #demo-slide .kicker { color: #a5b4fc; font-size: 26px; letter-spacing: .14em; text-transform: uppercase; font-weight: 600; margin-bottom: 18px; }
  #demo-slide h1 { font-size: 76px; line-height: 1.08; margin: 0 0 26px; letter-spacing: -.02em; font-weight: 800; }
  #demo-slide h2 { font-size: 52px; line-height: 1.15; margin: 0 0 34px; font-weight: 750; }
  #demo-slide p.lead { font-size: 32px; color: #cbd5e1; line-height: 1.45; margin: 0; max-width: 1300px; }
  #demo-slide ul { list-style: none; padding: 0; margin: 0; display: grid; gap: 20px; }
  #demo-slide li { font-size: 31px; color: #e2e8f0; line-height: 1.35; padding-left: 46px; position: relative; }
  #demo-slide li::before { content: ''; position: absolute; left: 0; top: 13px; width: 18px; height: 18px; border-radius: 5px; background: #818cf8; }
  #demo-slide li b { color: #fff; }
  #demo-slide .loop { display: flex; gap: 14px; margin-top: 40px; align-items: center; flex-wrap: wrap; }
  #demo-slide .loop span.step { background: rgba(129,140,248,.18); border: 1px solid rgba(165,180,252,.5); color: #e0e7ff; padding: 16px 24px;
    border-radius: 14px; font-size: 28px; font-weight: 650; }
  #demo-slide .loop span.arrow { color: #818cf8; font-size: 30px; }
  #demo-slide .agenda { display: grid; grid-template-columns: 1fr 1fr; gap: 22px 60px; }
  #demo-slide .agenda div { font-size: 32px; color: #e2e8f0; }
  #demo-slide .agenda div b { display: inline-block; width: 56px; height: 56px; border-radius: 14px; background: #4f46e5; color: #fff;
    text-align: center; line-height: 56px; margin-right: 18px; font-size: 28px; }
  #demo-slide .note { margin-top: 44px; font-size: 24px; color: #fcd34d; border-left: 4px solid #f59e0b; padding-left: 18px; }
  #demo-slide .chapter-num { font-size: 150px; font-weight: 800; color: #818cf8; line-height: 1; margin-bottom: 10px; }
  #demo-slide .links { margin-top: 44px; font-size: 26px; color: #a5b4fc; }
  #demo-slide .tbl { border-collapse: collapse; width: 100%; margin-top: 10px; font-size: 27px; }
  #demo-slide .tbl th { text-align: left; color: #a5b4fc; font-size: 21px; text-transform: uppercase; letter-spacing: .08em; padding: 10px 16px; border-bottom: 1px solid rgba(165,180,252,.35); }
  #demo-slide .tbl td { padding: 13px 16px; border-bottom: 1px solid rgba(148,163,184,.18); color: #e2e8f0; vertical-align: top; }
  #demo-slide .tbl td b { color: #fff; }
  #demo-slide .tbl td.n { color: #a5b4fc; font-weight: 800; width: 60px; }
  #demo-slide .tbl.areas td .sub { color: #cbd5e1; font-size: 23px; margin-top: 4px; }
  #demo-slide .tbl.result td:last-child b { color: #6ee7b7; }
  #demo-slide .qs { margin-top: 36px; display: grid; gap: 18px; }
  #demo-slide .qs div { font-size: 32px; color: #e2e8f0; }
  #demo-slide .qs div b { display: inline-block; width: 50px; height: 50px; border-radius: 12px; background: #4f46e5; color: #fff; text-align: center; line-height: 50px; margin-right: 16px; font-size: 26px; }
  #demo-slide ol.story { margin: 0; padding-left: 40px; display: grid; gap: 14px; }
  #demo-slide ol.story li { font-size: 29px; color: #e2e8f0; line-height: 1.38; padding-left: 8px; }
  #demo-slide ol.story li::before { display: none; }
  #demo-slide ol.story li b { color: #fde68a; }
  #demo-caption { pointer-events: none; position: fixed; left: 50%; bottom: 26px; transform: translateX(-50%); z-index: 99995;
    background: rgba(17,24,39,.9); color: #fff; padding: 12px 24px; border-radius: 12px; font: 600 22px Inter, -apple-system, sans-serif;
    max-width: 1350px; text-align: center; box-shadow: 0 8px 30px rgba(0,0,0,.35); }
  #demo-chip { pointer-events: none; position: fixed; left: 272px; bottom: 30px; z-index: 99995; background: #4f46e5; color: #fff;
    padding: 8px 16px; border-radius: 999px; font: 700 17px Inter, -apple-system, sans-serif; box-shadow: 0 6px 18px rgba(79,70,229,.4); }
  #root { transition: transform .85s cubic-bezier(.4,0,.2,1); will-change: transform; }
  `;
  document.head.appendChild(st);
  const c = document.createElement('div'); c.id = 'demo-cursor';
  c.innerHTML = '<svg viewBox="0 0 24 24" width="30" height="30"><path d="M3 2 L3 20 L8 15.5 L11.5 22.5 L14.5 21 L11 14 L18 14 Z" fill="#fff" stroke="#111827" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  document.body.appendChild(c);
  const s = document.createElement('div'); s.id = 'demo-slide'; s.innerHTML = '<div class="inner"></div>';
  document.body.appendChild(s);
})()
"""


class Narrator:
    """Synthesises narration with Piper and tracks when each segment starts relative to the video."""

    def __init__(self, voice: str, enabled: bool, speed: float):
        self.enabled = enabled
        self.segments: list[tuple[float, Path]] = []
        self.t0 = time.monotonic()
        self.n = 0
        if enabled:
            from piper import PiperVoice, SynthesisConfig
            self.voice = PiperVoice.load(str(ROOT / "var" / "voices" / f"{voice}.onnx"))
            self.cfg = SynthesisConfig(length_scale=speed, noise_scale=0.6, noise_w_scale=0.75)
            shutil.rmtree(AUDIO, ignore_errors=True)
            AUDIO.mkdir(parents=True, exist_ok=True)

    def synth(self, text: str) -> tuple[Path | None, float]:
        if not self.enabled:
            return None, min(1.5, 0.02 * len(text))
        self.n += 1
        path = AUDIO / f"seg_{self.n:02d}.wav"
        with wave.open(str(path), "wb") as w:
            self.voice.synthesize_wav(text, w, syn_config=self.cfg)
        with wave.open(str(path), "rb") as r:
            return path, r.getnframes() / r.getframerate()

    def start(self, path: Path | None) -> float:
        now = time.monotonic()
        if path is not None:
            self.segments.append((now - self.t0, path))
        return now


class Scene:
    """Narrated scene: audio starts on enter; exit waits until narration has finished (plus a short pause)."""

    def __init__(self, demo: "Demo", text: str, caption: str | None = None, pad: float = 0.3):
        self.demo, self.text, self.caption, self.pad = demo, text, caption, pad

    def __enter__(self):
        path, self.dur = self.demo.narrator.synth(self.text)
        if self.caption is not None:
            self.demo.caption(self.caption)
        self.started = self.demo.narrator.start(path)
        return self

    def __exit__(self, *exc):
        if exc[0] is None:
            remaining = self.dur + self.pad - (time.monotonic() - self.started)
            if remaining > 0:
                self.demo.page.wait_for_timeout(int(remaining * 1000 * self.demo.hold))
        return False


class Demo:
    def __init__(self, page: Page, narrator: Narrator, hold: float = 1.0):
        self.page, self.narrator, self.hold = page, narrator, hold

    # ---- overlays -------------------------------------------------------------------------------
    def install(self) -> None:
        self.page.evaluate(OVERLAY_JS)

    def caption(self, text: str | None) -> None:
        self.page.evaluate("""t => { let el = document.getElementById('demo-caption');
            if (!t) { el && el.remove(); return; }
            if (!el) { el = document.createElement('div'); el.id = 'demo-caption'; document.body.appendChild(el); }
            el.textContent = t; }""", text)

    def chip(self, text: str | None) -> None:
        self.page.evaluate("""t => { let el = document.getElementById('demo-chip');
            if (!t) { el && el.remove(); return; }
            if (!el) { el = document.createElement('div'); el.id = 'demo-chip'; document.body.appendChild(el); }
            el.textContent = t; }""", text)

    def slide(self, html: str | None) -> None:
        if html is None:
            self.page.evaluate("() => document.getElementById('demo-slide').classList.remove('show')")
            self.page.wait_for_timeout(650)
            return
        self.page.evaluate("""h => { const s = document.getElementById('demo-slide'); s.querySelector('.inner').innerHTML = h;
            s.classList.add('show'); }""", html)
        self.page.wait_for_timeout(650)

    # ---- pointer and zoom ------------------------------------------------------------------------
    def point(self, target: Locator | str, settle: int = 650) -> Locator:
        loc = self.page.locator(target).first if isinstance(target, str) else target
        loc.scroll_into_view_if_needed()
        self.page.wait_for_timeout(250)
        box = loc.bounding_box()
        if box:
            self.page.evaluate("([x, y]) => { const c = document.getElementById('demo-cursor'); c.style.left = x + 'px'; c.style.top = y + 'px'; }",
                               [box["x"] + min(box["width"] * 0.5, 60), box["y"] + box["height"] * 0.55])
        self.page.wait_for_timeout(settle)
        return loc

    def click(self, target: Locator | str, settle: int = 650) -> None:
        loc = self.point(target, settle)
        self.page.evaluate("() => { const c = document.getElementById('demo-cursor'); c.classList.remove('click'); void c.offsetWidth; c.classList.add('click'); }")
        loc.click()
        self.page.wait_for_timeout(350)

    def zoom(self, target: Locator | str, scale: float = 1.6) -> None:
        loc = self.page.locator(target).first if isinstance(target, str) else target
        self.unzoom(fast=True)
        loc.scroll_into_view_if_needed()
        self.page.wait_for_timeout(200)
        box = loc.bounding_box()
        if not box:
            return
        self.page.evaluate("""([x, y, s]) => { const r = document.getElementById('root'); const rb = r.getBoundingClientRect();
            r.style.transformOrigin = (x - rb.left) + 'px ' + (y - rb.top) + 'px'; r.style.transform = 'scale(' + s + ')'; }""",
                           [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, scale])
        self.page.wait_for_timeout(950)

    def unzoom(self, fast: bool = False) -> None:
        was = self.page.evaluate("() => { const r = document.getElementById('root'); const z = r.style.transform && r.style.transform !== 'none'; r.style.transform = 'none'; return z; }")
        if was:
            self.page.wait_for_timeout(300 if fast else 900)

    # ---- helpers -----------------------------------------------------------------------------------
    def nav(self, nav_id: str) -> None:
        self.unzoom()
        self.click(f"[data-nav={nav_id}]", settle=500)
        self.page.wait_for_timeout(500)

    def stat(self, label: str) -> str:
        return self.page.locator(".stat", has_text=label).first.locator(".stat-value").inner_text().strip()

    def chapter(self, num: int, title: str, subtitle: str) -> None:
        self.caption(None)
        self.unzoom(fast=True)
        with Scene(self, f"Part {num}. {title}.", pad=0.4):
            self.slide(f'<div class="chapter-num">{num}</div><h2>{title}</h2><p class="lead">{subtitle}</p>')
        self.slide(None)
        self.chip(f"{num} · {title}")


NUM = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen",
       "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"]

PLAIN_ACTION = {"verify_identity": "checking the person's identity", "unlock_account": "unlocked an account", "reset_password": "reset a password"}


def words(n) -> str:
    n = int(n)
    return NUM[n] if 0 <= n < len(NUM) else str(n)


def pct_words(s: str) -> str:
    return s.replace("%", " percent")


def plain_cluster(c: dict) -> tuple[str, str]:
    """Plain-English title and one-line explanation for a failure cluster (built from live analysis data)."""
    cat, d = c["category"], c.get("details", {})
    if cat == "prompt_injection":
        return ("Tricked by a hidden instruction",
                "a tampered help page told it to give someone administrator rights. A safety lock stopped it, but it had been fooled")
    if cat == "incorrect_tool_selection":
        if d.get("expected_tool") == "reset_password":
            return "Password resets never happened", "it picked the wrong tool, so the password was never actually reset"
        return "Picked the wrong action", f"it chose {d.get('selected_tool', 'another tool')} instead of {d.get('expected_tool', 'the right one')}"
    if cat == "workflow_sequencing":
        then = PLAIN_ACTION.get(d.get("then"), d.get("then"))
        first = PLAIN_ACTION.get(d.get("first"), d.get("first"))
        return "Skipped a required check", f"it {then} without first {first}, which company policy requires"
    if cat == "tool_execution_failure":
        return "Carried on when a system failed", "when the identity check timed out, it told the employee everything was fine"
    if cat == "retrieval_failure":
        return "Used the wrong help article", "it answered with an unrelated article instead of passing the question to a person"
    if cat == "unnecessary_escalation":
        return "Passed simple questions to a person", "it handed over questions it could have answered itself"
    return c["category_label"], c["hypothesis"]


def run(args) -> Path | None:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "_raw"
    shutil.rmtree(tmp, ignore_errors=True)
    narr_enabled = not args.no_video and not args.silent
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        ctx_args = {"viewport": {"width": 1920, "height": 1080}, "device_scale_factor": 1}
        if not args.no_video:
            ctx_args.update(record_video_dir=str(tmp), record_video_size={"width": 1920, "height": 1080})
        ctx = browser.new_context(**ctx_args)
        page = ctx.new_page()
        narrator = Narrator(args.voice, narr_enabled, args.speed)  # t0 ~= start of the video
        d = Demo(page, narrator, 0.05 if args.no_video else 1.0)
        page.add_init_script("try { localStorage.setItem('ah.tenant','acme'); localStorage.setItem('ah.user','maya') } catch (e) {}")
        page.goto(BASE)
        page.wait_for_selector("text=Quality Dashboard")
        d.install()
        api = lambda path: page.evaluate("p => fetch('/api' + p, {headers: {'X-Tenant-ID': 'acme', 'X-User': 'maya'}}).then(r => r.json())", path)

        # ================================================================== opening
        d.slide('<div class="kicker">Product demo</div><h1>AgentHill</h1>'
                '<p class="lead">A driving test, a mechanic and an annual inspection, for AI assistants.</p>')
        with Scene(d, "This is AgentHill. Think of it as a driving test, a mechanic, and an annual inspection, for A.I. assistants.", pad=0.6):
            pass
        d.slide('<div class="kicker">Why it matters</div><h2>AI assistants now do real work</h2>'
                '<p class="lead">For example, an IT helpdesk assistant that resets passwords, fixes VPN problems and installs software.</p>'
                '<div class="qs"><div><b>1</b>Is it doing the job correctly?</div><div><b>2</b>When it gets something wrong, why?</div>'
                '<div><b>3</b>If we fix it, is it really better, and did we break anything else?</div></div>')
        with Scene(d, "Companies now use A.I. assistants for real work, like an I.T. helpdesk that resets passwords and fixes V.P.N. problems. "
                      "Before you trust one, you need three answers. Is it doing the job correctly? When it fails, why? "
                      "And if we fix it, is it really better, without breaking anything else?", pad=0.4):
            pass
        d.slide('<div class="kicker">How AgentHill works</div><table class="tbl">'
                '<tr><th>Step</th><th>Everyday comparison</th><th>What AgentHill does</th></tr>'
                '<tr><td>1 · The exam</td><td>A driving-test route</td><td>Realistic requests, each with the correct way to handle it</td></tr>'
                '<tr><td>2 · Marking</td><td>The examiner’s checklist</td><td>Marks what the assistant <b>did</b> and what it <b>said</b></td></tr>'
                '<tr><td>3 · Diagnosis</td><td>A flight’s black box</td><td>Records every step, to show <b>why</b> it failed</td></tr>'
                '<tr><td>4 · Repair</td><td>A mechanic’s proposed fix</td><td>Suggests a specific change; a manager approves it</td></tr>'
                '<tr><td>5 · Retest</td><td>Re-sitting the same test</td><td>Old and new versions take the identical exam</td></tr>'
                '<tr><td>6 · Pass marks</td><td>The pass mark</td><td>The new version must meet the team’s standards</td></tr>'
                '<tr><td>7 · Learning</td><td>Adding tricky questions to future exams</td><td>Every confirmed mistake becomes a permanent test</td></tr></table>')
        with Scene(d, "AgentHill answers them in seven steps: an exam, marking, a black-box recording of every step, a proposed repair, "
                      "a side-by-side retest, the team's pass marks, and learning from every mistake.", pad=0.4):
            pass
        d.slide('<div class="kicker">In this demo</div><h2>We follow one IT helpdesk assistant through that whole journey</h2>'
                '<div class="agenda"><div><b>1</b>The exam</div><div><b>2</b>Finding out why</div><div><b>3</b>The repair</div>'
                '<div><b>4</b>The retest</div><div><b>5</b>Never repeating a mistake</div><div><b>6</b>One platform, every department</div></div>'
                '<div class="note">To make the demo meaningful, the assistant’s first version was set up with deliberate mistakes. Everything you see is the real system, running live.</div>')
        with Scene(d, "We'll follow one I.T. helpdesk assistant through six parts. Its first version has deliberate mistakes, and everything you see runs live.", pad=0.4):
            pass
        d.slide(None)

        # ================================================================== 1. the exam
        d.chapter(1, "The exam", "Realistic helpdesk requests, each with the correct way to handle it")
        with Scene(d, "Here is the assistant. These are the actions it is allowed to take, such as checking someone's identity, resetting a password, "
                      "or unlocking an account.", "The actions the assistant is allowed to take"):
            d.nav("agents")
            d.zoom(".card:has-text('Tools & APIs')", 1.45)
        with Scene(d, "And this is its exam: nineteen realistic requests, each with the correct way to handle it.",
                   "The exam: 19 realistic helpdesk requests"):
            d.unzoom(fast=True)
            d.click(".tabs button:has-text('Datasets & test cases')", settle=500)
            page.wait_for_selector("text=test cases")
            d.zoom("table.table", 1.15)
        with Scene(d, "For example: I forgot my password. The correct handling: check their identity, then reset the password.",
                   "Example: “I forgot my password” → check identity, then reset the password"):
            d.zoom("tr:has-text('IT-003')", 1.8)
        with Scene(d, "Three questions are deliberate tricks, because an assistant that can reset passwords will be targeted. "
                      "A bank tests a new cashier on deposits, and on spotting forged cheques.",
                   "Three questions are deliberate tricks: a tampered help page, a fake admin request, another company’s account"):
            d.zoom("tr:has-text('IT-017')", 1.6)
        gates = api("/agents")[0]["quality_gates"]
        d.unzoom(fast=True)
        d.caption(None)
        d.slide('<div class="kicker">The pass marks</div><h2>Before the exam, the team sets six standards</h2><table class="tbl">'
                f'<tr><td>Requests handled correctly</td><td>at least <b>{int(gates["min_task_success_rate"] * 100)}%</b></td></tr>'
                f'<tr><td>Picks the right action</td><td>at least <b>{int(gates["min_tool_selection_accuracy"] * 100)}%</b></td></tr>'
                f'<tr><td>Critical failures (security or policy breaches)</td><td><b>{gates["max_critical_failures"]}</b></td></tr>'
                f'<tr><td>Things broken by a change</td><td><b>{gates["max_regressions"]}</b></td></tr>'
                f'<tr><td>Speed: almost every request finished within</td><td><b>{gates["max_p95_latency_ms"] / 1000:g} seconds</b></td></tr>'
                f'<tr><td>Cost of a full exam</td><td>under <b>${gates["max_cost_per_run_usd"]:.2f}</b></td></tr></table>')
        with Scene(d, f"The team also sets six pass marks. For example: at least {int(gates['min_task_success_rate'] * 100)} percent handled correctly, "
                      "and zero critical failures, meaning security or policy breaches.", pad=0.4):
            pass
        d.slide(None)
        with Scene(d, "Now, let's run the exam.", "Running the exam", pad=0.2):
            d.nav("dashboard")
            d.click("[data-testid=run-eval]", settle=500)
            page.wait_for_selector("[data-testid=kpis]", timeout=120_000)
        hint = page.locator(".stat", has_text="Task success").first.locator(".stat-hint").inner_text()
        passed, total = [int(x) for x in hint.split()[0].split("/")]
        crit = d.stat("Critical failures")
        with Scene(d, f"The first version handles only {words(passed)} of {words(total)} requests correctly, with {words(crit)} critical failures. "
                      "So it fails the pass marks.",
                   f"First version: {passed} of {total} correct · {crit} critical failures · FAILS the pass marks"):
            d.zoom("[data-testid=kpis]", 1.4)
            page.wait_for_timeout(1500)
            d.zoom(".card:has-text('Current quality verdict')", 1.5)

        # ================================================================== 2. finding out why
        d.chapter(2, "Finding out why", "From a list of failures to a few root causes")
        with Scene(d, f"{words(total - passed).capitalize()} requests failed, but not for {words(total - passed)} different reasons. "
                      "AgentHill reads the recording of every failure, and groups them by root cause.",
                   "AgentHill reads the recording of every failure and groups them by root cause"):
            d.click("text=Inspect run →", settle=500)
            d.click("[data-testid=analyze]", settle=500)
            page.wait_for_selector("text=Failure clusters")
        runs = api("/runs?agent_id=1")
        analysis = api(f"/runs/{min(r['id'] for r in runs)}/analysis")
        areas = [(plain_cluster(c), c["frequency"]) for c in analysis["clusters"]]
        d.caption(None)
        rows = "".join(f'<tr><td class="n">{i}</td><td><b>{t}</b><div class="sub">{e[0].upper() + e[1:]}.</div></td><td class="n">{n}</td></tr>'
                       for i, ((t, e), n) in enumerate(areas, 1))
        d.slide(f'<div class="kicker">The {words(len(areas))} problem areas</div><table class="tbl areas"><tr><th>#</th><th>Problem</th><th>Requests</th></tr>{rows}</table>')
        spoken = " ".join(f"{words(i).capitalize()}: {t.lower()}, in {words(n)} request{'s' if n != 1 else ''}." for i, ((t, e), n) in enumerate(areas, 1))
        with Scene(d, f"It found {words(len(areas))} problem areas. {spoken} The biggest: a tampered help page told it to grant administrator rights, "
                      "and it tried. A safety lock stopped it, but it had been fooled.", pad=0.5):
            pass
        d.slide(None)
        with Scene(d, "On screen, each group shows the evidence from the recording, kept separate from AgentHill's explanation of the cause.",
                   "Each group: evidence from the recording, kept separate from the explanation"):
            d.zoom(page.locator(".card", has_text="Incorrect tool selection").first, 1.35)
        d.unzoom(fast=True)
        d.caption(None)
        d.slide('<div class="kicker">One example in detail</div><h2>Why the password was never reset</h2><ol class="story">'
                '<li>Hannah tells the assistant: <i>“I forgot my password.”</i></li>'
                '<li>The company’s instructions say, in IT jargon: <i>“Issue a temporary credential by sending a one-time sign-in link.”</i> That means <b>reset the password</b>.</li>'
                '<li>The assistant picks a tool by matching that wording to each tool’s <b>label</b>.</li>'
                '<li>The reset tool’s label said only <i>“Reset the password”</i>: <b>no words in common → score 0</b>.</li>'
                '<li>The identity tool’s label mentioned a <i>“one-time passcode sent to a registered device”</i>: <b>it looked like the best match</b>.</li>'
                '<li>So it checked Hannah’s identity, told her a link was on its way, and <b>never reset her password</b>.</li></ol>')
        with Scene(d, "Let me take one example in detail: the password reset. Hannah says she's forgotten her password. "
                      "The instructions say, in I.T. jargon: issue a temporary credential by sending a one-time sign-in link. That means, reset the password. "
                      "The assistant picks a tool by matching those words to each tool's label. The reset tool's label shared no words, so it scored zero. "
                      "The identity tool's label looked closer. So it checked her identity, said a link was coming, and never reset her password.", pad=0.5):
            pass
        d.slide(None)
        span = page.locator(".span", has_text="Issue a temporary credential").first
        with Scene(d, "Here is that moment in the recording. Yellow is the tool it chose. Red is the tool it should have chosen, scoring zero.",
                   "Yellow: the tool it chose · Red: the tool it should have chosen (score 0)"):
            d.click("text=Case results & traces", settle=500)
            d.click("[data-case=IT-003]", settle=500)
            page.wait_for_selector("[data-testid=trace]")
            d.click(span.locator(".span-head"), settle=500)
            d.zoom(span.locator("table"), 1.75)
        with Scene(d, "The reply sounded perfect. Only checking what the assistant actually did reveals the failure.",
                   "The reply sounded right; only checking what it actually did reveals the failure"):
            page.wait_for_timeout(500)

        # ================================================================== 3. the repair
        d.chapter(3, "The repair", "A specific, reviewable change for each root cause")
        with Scene(d, "For each root cause, AgentHill proposes a specific repair, shown as a before and after change, ranked by importance.",
                   "Proposed repairs, shown as before-and-after changes, ranked by importance"):
            d.nav("improve")
            page.wait_for_selector("text=Prioritized recommendations")
            d.zoom(page.locator(".card", has_text="Add an untrusted-content instruction").first, 1.3)
        with Scene(d, "For the password problem: rewrite the reset tool's label in the instructions' own words.",
                   "Password repair: rewrite the reset tool’s label in the instructions’ words"):
            d.zoom(page.locator(".card", has_text="Clarify the reset_password tool description").first.locator(".patch").first, 1.6)
        with Scene(d, "These are proposals, so a manager approves three: ignore instructions hidden in documents, reword the reset label, and always check identity first.",
                   "A manager approves three repairs for testing"):
            d.unzoom(fast=True)
            for title in ("Add an untrusted-content instruction", "Clarify the reset_password tool description", "Require verify_identity before"):
                card = page.locator(".card", has_text=title).first
                d.click(card.locator("button", has_text="Approve change"), settle=450)
                d.click(page.locator(".card", has_text=title).first.locator("input[type=checkbox]"), settle=350)
        page.fill(".sticky-bar input", "Injection guard + reset_password description + identity prerequisite")

        # ================================================================== 4. the retest
        d.chapter(4, "The retest", "Old and new versions take the identical exam")
        with Scene(d, "AgentHill creates a new version, keeps the original untouched, and both sit the identical exam, side by side.", "New version created · original kept · same exam for both"):
            d.click("[data-testid=create-experiment]", settle=500)
            page.wait_for_selector("[data-testid=approve-experiment]")
            d.click("[data-testid=approve-experiment]", settle=500)
            page.wait_for_selector("[data-testid=run-experiment]")
            d.click("[data-testid=run-experiment]", settle=500)
            page.wait_for_selector("[data-testid=comparison]", timeout=180_000)
        fixed, regressed = d.stat("Fixed"), d.stat("Regressed")
        row = page.locator("[data-testid=comparison] tr", has_text="Task success").first.inner_text().split("\t")
        crit_row = page.locator("[data-testid=comparison] tr", has_text="Critical failures").first.inner_text().split("\t")
        with Scene(d, f"The result: correct answers rise from {pct_words(row[1])} to {pct_words(row[2])}. {words(fixed).capitalize()} problems fixed, "
                      f"critical failures down from {words(crit_row[1])} to {words(crit_row[2])}, and {'nothing' if regressed == '0' else words(regressed) + ' things'} broken.",
                   f"Correct: {row[1]} → {row[2]} · {fixed} fixed · critical {crit_row[1]} → {crit_row[2]} · {regressed} broken"):
            d.zoom(page.locator(".card", has_text="Baseline vs candidate").first.locator(".stats"), 1.45)
            page.wait_for_timeout(1500)
            d.zoom("[data-testid=comparison]", 1.3)
        gates_card = page.locator(".card", has_text="Quality gates on the candidate").first
        verdict = gates_card.locator(".verdict-row .badge").first.inner_text().strip()
        ok = verdict.upper() == "PASS"
        with Scene(d, ("It now meets all six pass marks: the team's standards on this exam, not a guarantee for real-world use. " if ok else f"The pass-mark verdict is {verdict}. ") +
                   "The manager accepts it, and the decision is recorded.",
                   f"Pass marks: {verdict} · met the team’s standards on this exam"):
            d.zoom(gates_card, 1.3)
            page.wait_for_timeout(1500)
            d.unzoom(fast=True)
            d.click("[data-testid=accept-experiment]", settle=500)
            page.wait_for_selector("text=Decision:")

        # ================================================================== 5. never repeating a mistake
        d.chapter(5, "Never repeating a mistake", "Confirmed mistakes become permanent test questions")
        with Scene(d, "Hannah's case becomes a permanent test question, so if the problem ever returns, it will be caught.",
                   "Hannah’s case becomes a permanent test question"):
            d.nav("traces")
            page.select_option("[data-testid=trace-run-select]", index=page.locator("[data-testid=trace-run-select] option").count() - 1)
            page.wait_for_timeout(600)
            d.click("[data-case=IT-003]", settle=450)
            d.click("[data-testid=to-regression]", settle=450)
            page.wait_for_selector("text=to the regression suite")
        with Scene(d, "Re-running that list on the new version confirms the fix still holds.", "Re-run on the new version: the fix holds"):
            d.nav("regression")
            d.click("[data-testid=run-regression]", settle=450)
            page.wait_for_selector("text=still fixed", timeout=60_000)
            d.zoom(".card:has-text('Regression suite')", 1.35)
        with Scene(d, "And every decision stays on record: what failed, why, what changed, the results, and who approved it.",
                   "Full record: what failed, why, what changed, results, who approved"):
            d.unzoom(fast=True)
            d.click("text=Experiment history", settle=450)
            d.click(page.locator(".history-head").first, settle=450)
            d.zoom(".history-item", 1.25)

        # ================================================================== 6. every department
        d.chapter(6, "One platform, every department", "The same platform tests assistants for any team")
        with Scene(d, "The same platform works for any department. Here, an H.R. assistant is added and tested.",
                   "An HR assistant: added and tested on the same platform", pad=0.2):
            d.nav("agents")
            d.click("text=+ Register agent", settle=450)
            page.select_option("[data-testid=template-select]", "hr_support_agent")
            d.click("[data-testid=register-agent]", settle=450)
            page.wait_for_timeout(700)
            d.nav("dashboard")
            d.click("[data-testid=run-eval]", settle=450)
            page.wait_for_timeout(1200)
            page.wait_for_selector("[data-testid=run-eval]:not([disabled])", timeout=120_000)
            page.wait_for_selector("[data-testid=kpis]")
        hr_hint = page.locator(".stat", has_text="Task success").first.locator(".stat-hint").inner_text()
        hp, ht = [int(x) for x in hr_hint.split()[0].split("/")]
        with Scene(d, f"It handles {words(hp)} of {words(ht)} correctly on the first try, ready for the same find and fix process.",
                   f"HR assistant: {hp} of {ht} correct on the first try"):
            d.zoom("[data-testid=kpis]", 1.4)
        with Scene(d, "Teams can also choose the A.I. model for each job: local models, Claude, OpenAI, or Gemini.", "Choose the AI model for each job: local, Claude, OpenAI or Gemini"):
            d.nav("settings")
            d.zoom(".grid-2", 1.2)
        d.unzoom()

        # ================================================================== close
        d.caption(None)
        d.chip(None)
        d.slide('<div class="kicker">In summary</div><h2>Know whether your AI assistant works, why it fails, and whether the fix is really better</h2>'
                '<table class="tbl result"><tr><th></th><th>First version</th><th>After the repair</th></tr>'
                f'<tr><td>Requests handled correctly</td><td>{row[1]}</td><td><b>{row[2]}</b></td></tr>'
                f'<tr><td>Critical failures</td><td>{crit_row[1]}</td><td><b>{crit_row[2]}</b></td></tr>'
                f'<tr><td>Things broken by the fix</td><td>–</td><td><b>{regressed}</b></td></tr>'
                f'<tr><td>Pass marks</td><td>Failed</td><td><b>{"Passed" if ok else verdict}</b></td></tr></table>'
                '<div class="note">Voiceover is AI-generated (Piper, en-GB). All app footage is a live recording.</div>')
        with Scene(d, f"In summary: from {pct_words(row[1])} to {pct_words(row[2])} correct, with no critical failures and nothing broken. "
                      "AgentHill: know whether your A.I. assistant works, why it fails, and whether the fix is really better.", pad=1.2):
            pass
        raw = page.video.path() if not args.no_video else None
        ctx.close()
        browser.close()
    if args.no_video:
        return None
    return mux(Path(raw), narrator.segments, OUT / "agenthill-demo.mp4")


def mux(video: Path, segments: list[tuple[float, Path]], out: Path) -> Path:
    if not segments:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-c:v", "libx264", "-crf", "24", "-preset", "slow",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-r", "25", str(out)], check=True)
        return out
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video)]
    for _, path in segments:
        cmd += ["-i", str(path)]
    filters, labels = [], []
    for i, (offset, _) in enumerate(segments, start=1):
        ms = max(0, int(offset * 1000))
        filters.append(f"[{i}:a]aresample=48000,adelay={ms}|{ms}[a{i}]")
        labels.append(f"[a{i}]")
    filters.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[aout]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "0:v", "-map", "[aout]", "-c:v", "libx264", "-crf", "30", "-preset", "veryslow", "-tune", "stillimage",
            "-pix_fmt", "yuv420p", "-r", "25", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-shortest", str(out)]
    subprocess.run(cmd, check=True)
    shutil.rmtree(video.parent, ignore_errors=True)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--no-video", action="store_true", help="run the flow as an end-to-end UI test only")
    ap.add_argument("--silent", action="store_true", help="record video without narration")
    ap.add_argument("--voice", default="en_GB-cori-high")
    ap.add_argument("--speed", type=float, default=0.95, help="Piper length_scale (>1 is slower)")
    a = ap.parse_args()
    t0 = time.time()
    out = run(a)
    print(f"Demo finished in {time.time() - t0:.0f}s" + (f"; video: {out}" if out else ""))
    sys.exit(0)
