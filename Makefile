PY=.venv/bin/python
setup:
	python3 -m venv .venv && $(PY) -m pip install -r requirements-dev.txt && $(PY) -m playwright install chromium
	cd frontend && npm ci && npx vite build
demo-data:
	$(PY) scripts/prepare_demo.py
run:
	.venv/bin/uvicorn --app-dir backend agenthill.main:app --port 8000
test:
	$(PY) -m pytest -q
loop:
	$(PY) scripts/run_full_loop.py
voices:
	.venv/bin/pip install -r requirements-demo.txt && $(PY) -m piper.download_voices --download-dir var/voices en_GB-cori-high
record:
	$(PY) scripts/prepare_demo.py && $(PY) scripts/record_demo.py
