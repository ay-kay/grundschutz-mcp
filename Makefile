.PHONY: help venv install fetch-xml fetch-krt schema xml krt crossref embed build build-full serve docker-up docker-up-traefik docker-down test clean

PY  ?= .venv/bin/python
PIP ?= .venv/bin/pip
DB  ?= db/grundschutz.db

help:
	@echo "grundschutz-mcp targets:"
	@echo ""
	@echo "  Local dev:"
	@echo "    make venv         - create .venv and install dependencies"
	@echo "    make fetch-xml    - download the BSI Kompendium 2023 XML (~3 MB)"
	@echo "    make fetch-krt    - download the BSI KRT 2023 XLSX (~350 KB)"
	@echo "    make schema       - reset DB and apply schema/schema.sql"
	@echo "    make xml          - import structure + prose from the XML"
	@echo "    make krt          - import requirement<->threat M:N + C/I/A"
	@echo "    make crossref     - extract cross-references from prose"
	@echo "    make embed        - generate embeddings (~2 GB download first run)"
	@echo "    make build        - fetch-xml + fetch-krt + schema + xml + krt + crossref"
	@echo "    make build-full   - build + embed"
	@echo "    make serve        - start the server on localhost:8080"
	@echo "    make test         - run pytest"
	@echo "    make clean        - remove DB and pytest cache"
	@echo ""
	@echo "  Docker (standalone, port 127.0.0.1:8080 by default):"
	@echo "    make docker-up        - docker compose up -d --build"
	@echo "    make docker-down      - docker compose down"
	@echo ""
	@echo "  Docker (Traefik overlay, requires PUBLIC_HOST in .env):"
	@echo "    make docker-up-traefik - up with compose.traefik.yml layered on"

venv:
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev,embeddings,server]"

install: venv

fetch-xml:
	./scripts/fetch-bsi-xml.sh

fetch-krt:
	./scripts/fetch-bsi-krt.sh

schema:
	$(PY) -m grundschutz_mcp.build schema --db $(DB)

xml:
	$(PY) -m grundschutz_mcp.build xml --db $(DB)

krt:
	$(PY) -m grundschutz_mcp.build krt --db $(DB)

crossref:
	$(PY) -m grundschutz_mcp.build crossref --db $(DB)

embed:
	$(PY) -m grundschutz_mcp.build embed --db $(DB)

build: fetch-xml fetch-krt
	$(PY) -m grundschutz_mcp.build all --db $(DB)

build-full: fetch-xml fetch-krt
	$(PY) -m grundschutz_mcp.build all-with-embeddings --db $(DB)

serve:
	GRUNDSCHUTZ_DB=$(DB) $(PY) -m grundschutz_mcp.server

docker-up:
	docker compose up -d --build

docker-up-traefik:
	docker compose -f compose.yml -f compose.traefik.yml up -d --build

docker-down:
	docker compose down

test:
	.venv/bin/pytest -v

clean:
	rm -f $(DB) $(DB)-wal $(DB)-shm
	rm -rf .pytest_cache
