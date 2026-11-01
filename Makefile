.PHONY: install test lint demo tune bench bench20 llm-subset clean

PY := .venv/bin/python
CLI := .venv/bin/shaudit

install:
	uv venv -q --python 3.13 || true
	uv pip install -q -e ".[dev,llm]"

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check src tests

demo:
	$(CLI) demo
	$(CLI) demo --flaws enqueue_only cache_carries_auth

tune:
	$(CLI) tune --out results/tuned.json

bench:
	$(CLI) bench --out results --budget 10

bench20:
	mkdir -p results_b20 && cp results/tuned.json results_b20/ && $(CLI) bench --out results_b20 --budget 20

PROVIDER ?= anthropic

llm-subset:
	$(CLI) llm-subset --n 4 --provider $(PROVIDER)

clean:
	rm -rf .venv .pytest_cache .ruff_cache results_untuned
