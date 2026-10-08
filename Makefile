TOOLKIT ?= python3 -m toolkit.cli.app
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

.PHONY: download-gcs
download-gcs:
	@DIR=out/data/mart/legal_graph/$(YEAR); \
	mkdir -p $$DIR; \
	BASE=https://storage.googleapis.com/dataciviclab-mart/legal-graph/legal_graph/$(YEAR); \
	for t in mart_legal_nodes mart_legal_edges mart_legal_node_metrics \
	         mart_legal_search_keys mart_legal_node_rel mart_legal_emend_leg; do \
		curl -fsSL "$$BASE/$$t.parquet" -o "$$DIR/$$t.parquet" && echo "✅ $$t" || exit 1; \
	done

.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:' Makefile | sort
