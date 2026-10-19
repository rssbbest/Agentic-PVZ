PYTHON := .venv/bin/python
.PHONY: setup start portable stop status build build-webgl test smoke smoke-llm unity-smoke
setup:
	bash scripts/setup.sh
start:
	$(PYTHON) -m integration.manage start --mode unity
portable:
	$(PYTHON) -m integration.manage start --mode portable
stop:
	$(PYTHON) -m integration.manage stop
status:
	$(PYTHON) -m integration.manage status
build:
	$(PYTHON) -m integration.unity build
build-webgl:
	$(PYTHON) -m integration.unity build --target webgl
test:
	PYTHONPATH=autopilot-backend $(PYTHON) -m pytest -q autopilot-sdk/tests integration/tests autopilot-backend/tests
smoke:
	$(PYTHON) -m integration.live_test --skip-llm
smoke-llm:
	$(PYTHON) -m integration.live_test
unity-smoke:
	$(PYTHON) -m integration.unity_smoke
