"""Reset the database and seed the starting state for the recorded demonstration.

Seeds only *configuration*, never results:
  * Acme Corp: IT Incident Resolution Agent (intentionally flawed v1) + its golden dataset v1
  * Globex Inc: its own IT agent and dataset (used to demonstrate customer isolation)
No evaluation runs, analyses or experiments are created - everything shown in the demo is produced live.

Usage:  python scripts/prepare_demo.py [--keep-settings]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from agenthill import db as D  # noqa: E402
from agenthill import services as S  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-hr", action="store_true", help="also register the HR agent (the demo registers it live in the UI)")
    ap.add_argument("--if-empty", action="store_true", help="seed only when the database has no agents (used by Docker)")
    args = ap.parse_args()
    D.init_engine()
    if args.if_empty:
        s = D.SessionLocal()
        has_agents = s.query(D.Agent).count() > 0
        s.close()
        if has_agents:
            print("Database already initialised; leaving it unchanged.")
            return
    D.reset_db()
    s = D.SessionLocal()
    S.ensure_tenants(s)
    it = S.register_from_template(s, "acme", "leo", "it_incident_agent")
    if args.with_hr:
        S.register_from_template(s, "acme", "leo", "hr_support_agent")
    gx = S.register_from_template(s, "globex", "leo", "it_incident_agent")
    gx.name = "Globex IT Service Agent"
    s.commit()
    print(f"Seeded: Acme agent #{it.id} ({it.name}); Globex agent #{gx.id}. No runs were created.")
    print(f"Database: {D.SessionLocal.kw['bind'].url}")


if __name__ == "__main__":
    main()
