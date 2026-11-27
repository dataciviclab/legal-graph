TOOLKIT ?= python3 -m toolkit.cli.app
YEAR ?= 2026
PYTHON ?= python3

# Due dataset sotto compose/, due budget di memoria:
# - legal-graph: nodi+archi+derivati (~62M token NON li tocca più)
# - legal-index: l'indice BM25, che aggrega ~62M token prima di ridurli
#   a 7,2M coppie (atto, stem) ed è il picco del compose storico.
# `run_mart` non legge `duckdb.memory_limit` (solo il clean): il tetto si
# passa da env, letta da info lab-connectors.safe_connect.
GRAPH_CONFIG ?= compose/legal-graph/dataset.yml
TEXT_CONFIG ?= compose/legal-texts/dataset.yml
INDEX_CONFIG ?= compose/legal-index/dataset.yml
# `DUCKDB_MEMORY_LIMIT` da ambiente vince su entrambi (utile in locale per
# provare il tetto senza toccare il Makefile): make importa le env var.
GRAPH_MEMORY ?= $(if $(DUCKDB_MEMORY_LIMIT),$(DUCKDB_MEMORY_LIMIT),2GB)
INDEX_MEMORY ?= $(if $(DUCKDB_MEMORY_LIMIT),$(DUCKDB_MEMORY_LIMIT),3GB)
# 4 thread sull'indice alzano il picco sopra i 3GB (buffer per-thread):
# 3 thread sono il punto in cui il build completo entra in ~3,2GB.
INDEX_THREADS ?= 3

.PHONY: run run-graph run-texts run-index run-local freshness check lint clean clean-cache test download-gcs download-graph download-texts download-index help

# Ordine obbligato: l'indice legge search_keys dal grafo. `legal-texts` è
# indipendente (legge solo clean di costituzione-italiana): lo eseguiamo
# prima dell'indice per avere il publish completo, ma non dipende dal grafo.
run:
	$(MAKE) run-graph
	$(MAKE) run-texts
	$(MAKE) run-index

run-graph:
	DUCKDB_MEMORY_LIMIT=$(GRAPH_MEMORY) $(TOOLKIT) run mart -c $(GRAPH_CONFIG) -y $(YEAR)
	$(PYTHON) -m legal_graph.freshness --config $(GRAPH_CONFIG) --out out/data/mart/legal_graph/$(YEAR)/mart_legal_freshness.parquet

run-texts:
	DUCKDB_MEMORY_LIMIT=$(GRAPH_MEMORY) $(TOOLKIT) run mart -c $(TEXT_CONFIG) -y $(YEAR)

# Solo la tabella di freschezza (non ricostruisce il grafo).
freshness:
	$(PYTHON) -m legal_graph.freshness --config $(GRAPH_CONFIG) --out out/data/mart/legal_graph/$(YEAR)/mart_legal_freshness.parquet

run-index:
	DUCKDB_MEMORY_LIMIT=$(INDEX_MEMORY) DUCKDB_THREADS=$(INDEX_THREADS) $(TOOLKIT) run mart -c $(INDEX_CONFIG) -y $(YEAR)

# Macchine piccole (<8GB liberi): il grafo si costruisce in locale (~2,4GB),
# l'indice BM25 no (supera i 3GB anche da solo: `regexp_extract_all` +
# `UNNEST` non spillano). Si scarica l'ultimo pubblicato e si itera sul resto.
run-local:
	$(MAKE) run-graph
	$(MAKE) run-texts
	$(MAKE) download-index

check:
	$(TOOLKIT) run preflight -c $(GRAPH_CONFIG)
	$(TOOLKIT) run preflight -c $(TEXT_CONFIG)
	$(TOOLKIT) run preflight -c $(INDEX_CONFIG)

lint:
	$(PYTHON) -m ruff check legal_graph tests

clean:
	rm -rf out/data/_runs out/data/mart

clean-cache:
	rm -rf data/text_cache

test:
	$(PYTHON) -m pytest tests/ -q

download-gcs: download-graph download-texts download-index

download-graph:
	@DIR=out/data/mart/legal_graph/$(YEAR); mkdir -p $$DIR; \
	BASE=https://storage.googleapis.com/dataciviclab-mart/legal-graph/legal_graph/$(YEAR); \
	for t in mart_legal_nodes mart_legal_edges mart_legal_entity_alias \
	         mart_legal_relation_type mart_legal_coverage \
	         mart_legal_node_metrics mart_legal_search_keys mart_legal_node_rel \
	         mart_legal_emend_leg; do \
		curl -fsSL "$$BASE/$$t.parquet" -o "$$DIR/$$t.parquet.tmp" \
			&& mv "$$DIR/$$t.parquet.tmp" "$$DIR/$$t.parquet" \
			&& echo "✅ $$t" || { rm -f "$$DIR/$$t.parquet.tmp"; exit 1; }; \
	done; \
	# `mart_legal_freshness` è prodotta dal repo stesso e compare solo dopo il
	# primo run: se manca su GCS non è un errore (il file locale resta).
	# `texts` e `massime` vivono nel dataset `legal_texts` (download-texts).
	for t in mart_legal_edges_in mart_legal_freshness; do \
		if curl -fsSL "$$BASE/$$t.parquet" -o "$$DIR/$$t.parquet.tmp"; then \
			mv "$$DIR/$$t.parquet.tmp" "$$DIR/$$t.parquet"; echo "✅ $$t"; \
		else \
			rm -f "$$DIR/$$t.parquet.tmp"; \
			echo "⚠️  $$t non ancora su GCS — resta il file locale se presente"; \
		fi; \
	done

download-texts:
	@DIR=out/data/mart/legal_texts/$(YEAR); mkdir -p $$DIR; \
	BASE=https://storage.googleapis.com/dataciviclab-mart/legal-graph/legal_texts/$(YEAR); \
	for t in mart_legal_texts mart_legal_massime; do \
		if curl -fsSL "$$BASE/$$t.parquet" -o "$$DIR/$$t.parquet.tmp"; then \
			mv "$$DIR/$$t.parquet.tmp" "$$DIR/$$t.parquet"; echo "✅ $$t"; \
		else \
			rm -f "$$DIR/$$t.parquet.tmp"; \
			echo "⚠️  $$t non ancora su GCS — resta il file locale se presente"; \
		fi; \
	done

download-index:
	@IDX=out/data/mart/legal_index/$(YEAR); mkdir -p $$IDX; \
	IBASE=https://storage.googleapis.com/dataciviclab-mart/legal-graph/legal_index/$(YEAR); \
	if curl -fsSL "$$IBASE/mart_legal_search_terms.parquet" -o "$$IDX/mart_legal_search_terms.parquet.tmp"; then \
		mv "$$IDX/mart_legal_search_terms.parquet.tmp" "$$IDX/mart_legal_search_terms.parquet"; \
		echo "✅ mart_legal_search_terms (legal_index)"; \
	else \
		rm -f "$$IDX/mart_legal_search_terms.parquet.tmp"; \
		echo "⚠️  mart_legal_search_terms non ancora su GCS"; \
	fi

help:
	@grep -E '^[a-zA-Z_-]+:' Makefile | sort
