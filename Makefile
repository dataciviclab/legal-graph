TOOLKIT ?= toolkit
YEAR ?= 2026
PYTHON ?= python3

.PHONY: run
run:
	$(TOOLKIT) run mart -c dataset.yml -y $(YEAR)

.PHONY: check
check:
	$(TOOLKIT) run preflight -c dataset.yml

.PHONY: lint
lint:
	$(PYTHON) -m ruff check legal_graph tests scripts

.PHONY: intelligence
intelligence:
	@if [ -f out/data/mart/legal_graph/$(YEAR)/mart_legal_node_metrics.parquet ]; then \
		echo "metrics gia' nel mart compose (make run)"; \
	else \
		$(PYTHON) scripts/graph_intelligence.py; \
	fi

.PHONY: clean
clean:
	rm -rf out/data/_runs out/data/mart

.PHONY: clean-cache
clean-cache:
	rm -rf data/text_cache

.PHONY: test
test:
	$(PYTHON) -m pytest tests/ -q

.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:' Makefile | sort
