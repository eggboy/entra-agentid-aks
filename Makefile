# ── agentid-aks Makefile ──────────────────────────────────
#
# This is a uv workspace with separate pyproject.toml per service:
#   root/pyproject.toml       → workspace config, shared tooling (ruff, pytest)
#   backend/pyproject.toml    → backend dependencies
#   frontend/pyproject.toml   → frontend dependencies
#
# To add a dependency:
#   cd backend  && uv add <pkg>     # NOT from root
#   cd frontend && uv add <pkg>
#
# Builds use ACR Tasks (az acr build) to avoid arm64/amd64 mismatch
# when developing on Apple Silicon Macs.
# ──────────────────────────────────────────────────────────

ACR_NAME   ?= crjay
NAMESPACE  ?= agentid-demo
IMAGE_TAG  ?= latest

# ── Local dev ────────────────────────────────────────────
.PHONY: install dev-backend dev-frontend test lint deps

install:
	uv sync

dev-backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd frontend && uv run uvicorn app.main:app --reload --port 3000

test:
	cd backend  && uv run pytest -q
	cd frontend && uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .

deps:
	cd backend  && uv run deptry .
	cd frontend && uv run deptry .

# ── ACR build (runs on Azure, always linux/amd64) ───────
.PHONY: build-backend build-frontend build-all

build-backend:
	az acr build \
		--registry $(ACR_NAME) \
		--image agentid-backend:$(IMAGE_TAG) \
		--file Dockerfile.backend \
		.

build-frontend:
	az acr build \
		--registry $(ACR_NAME) \
		--image agentid-frontend:$(IMAGE_TAG) \
		--file Dockerfile.frontend \
		.

build-all: build-backend build-frontend

# ── Deploy (restart pods to pull latest image) ──────────
.PHONY: deploy-backend deploy-frontend deploy-all

deploy-backend:
	kubectl -n $(NAMESPACE) rollout restart deployment/backend
	kubectl -n $(NAMESPACE) rollout status deployment/backend --timeout=120s

deploy-frontend:
	kubectl -n $(NAMESPACE) rollout restart deployment/frontend
	kubectl -n $(NAMESPACE) rollout status deployment/frontend --timeout=120s

deploy-all: deploy-backend deploy-frontend

# ── Shortcuts ────────────────────────────────────────────
.PHONY: ship-backend ship-frontend ship-all

ship-backend: build-backend deploy-backend    ## Build + deploy backend
ship-frontend: build-frontend deploy-frontend ## Build + deploy frontend
ship-all: build-all deploy-all                ## Build + deploy everything

# ── Diagnostics ──────────────────────────────────────────
.PHONY: status logs-backend logs-frontend

status:
	kubectl -n $(NAMESPACE) get pods -o wide

logs-backend:
	kubectl -n $(NAMESPACE) logs -l app=backend -c backend --tail=50

logs-frontend:
	kubectl -n $(NAMESPACE) logs -l app=frontend --tail=50
