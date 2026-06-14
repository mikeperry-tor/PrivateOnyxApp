ENV_FILE ?= .env.wrapper
WRAPPER_FILE := docker-compose.yaml
FULL_OVERRIDE_FILE := docker-compose.full.yml
LITE_OVERRIDE_FILE := docker-compose.lite.yml

MYST_NODE_REPO ?= https://github.com/mikeperry-tor/node.git
MYST_NODE_BRANCH ?= docker_host_fixes_with_logs
MYST_DOCKERFILE ?= myst/build/Dockerfile
MYST_IMAGE ?= mysteriumnetwork/myst:docker_host_fixes_with_logs

TEEP_REPO ?= https://github.com/13rac1/teep.git
TEEP_REF ?= main
TEEP_DOCKERFILE ?= teep/build/Dockerfile
TEEP_IMAGE ?= 13rac1/teep:main
TAILSCALE_IMAGE ?= tailscale/tailscale:stable
CONTAINER_BIN ?= $(strip $(shell sed -n 's/^CONTAINER_BIN=//p' "$(ENV_FILE)" 2>/dev/null | head -1 | sed 's/^"//; s/"$$//'))
PODMAN_COMPOSE_PROVIDER ?= podman
ifeq ($(strip $(CONTAINER_BIN)),)
CONTAINER_BIN := docker
endif
export CONTAINER_BIN
export PODMAN_COMPOSE_PROVIDER

# Source of truth: ONYX_IMAGE_TAG in $(ENV_FILE). Allow CLI override.
ONYX_IMAGE_TAG ?= $(strip $(shell sed -n 's/^ONYX_IMAGE_TAG=//p' "$(ENV_FILE)" 2>/dev/null | head -1))
ifeq ($(strip $(ONYX_IMAGE_TAG)),)
$(error ONYX_IMAGE_TAG is not set. Add ONYX_IMAGE_TAG=... to $(ENV_FILE), or pass ONYX_IMAGE_TAG=... on the make command line)
endif
ONYX_BACKEND_IMAGE ?= onyxdotapp/onyx-backend:$(ONYX_IMAGE_TAG)
ONYX_WEB_SERVER_IMAGE ?= onyxdotapp/onyx-web-server:$(ONYX_IMAGE_TAG)
ONYX_MODEL_SERVER_IMAGE ?= onyxdotapp/onyx-model-server:$(ONYX_IMAGE_TAG)
ONYX_INSTALL_SCRIPT ?= ./install.sh
ONYX_INSTALL_WRAPPER ?= ./install-with-container-bin.sh
ONYX_ENV_FILE ?= onyx/onyx_data/deployment/.env
ONYX_CONFIG_REF ?= $(ONYX_IMAGE_TAG)
ONYX_INSTALL_HOST_PORT_80 ?= 3001
SEARXNG_COMPOSE_FILE := searxng/docker-compose.yml
SEARXNG_ENV_FILE := searxng/.env
EMBEDSERV_DIR := embedserv
EMBEDSERV_REQUIREMENTS := $(EMBEDSERV_DIR)/requirements.txt
EMBEDSERV_VENV := $(EMBEDSERV_DIR)/.venv
EMBEDSERV_MODEL_CACHE := $(EMBEDSERV_DIR)/models

LITE_FILES := $(WRAPPER_FILE):$(LITE_OVERRIDE_FILE)
FULL_FILES := $(WRAPPER_FILE):$(FULL_OVERRIDE_FILE)

.PHONY: help up-lite up-full down-lite down-full ps-lite ps-full logs-lite logs-full ensure-onyx-config init-onyx-env sync-onyx-env upgrade upgrade-onyx searxng-image-ready tailscale-image-ready myst-image-ready myst-build teep-image-ready teep-build onyx-image-ready onyx-build embedserv-install embedserv-verify-model embedserv-serve

help:
	@echo "Targets:"
	@echo "  make up-lite      # Start wrapper + Onyx lite"
	@echo "  make up-full      # Start wrapper + Onyx full"
	@echo "  make down-lite    # Stop wrapper + Onyx lite"
	@echo "  make down-full    # Stop wrapper + Onyx full"
	@echo "  make ps-lite      # Show lite mode containers"
	@echo "  make ps-full      # Show full mode containers"
	@echo "  make logs-lite    # Tail lite mode logs"
	@echo "  make logs-full    # Tail full mode logs"
	@echo "  make upgrade      # Rebuild Myst + teep images, refresh Onyx deployment files, pull SearxNG, and pull Tailscale"
	@echo "  make upgrade-onyx # Download fresh Onyx deployment files for ONYX_IMAGE_TAG"
	@echo "  make onyx-build   # Pull/build Onyx images via onyx/install.sh"
	@echo "  make myst-build   # Build Myst image from myst/build/Dockerfile"
	@echo "  make teep-build   # Build teep image from teep/build/Dockerfile"
	@echo "  make embedserv-install # Create embedserv venv with uv and download the MLX embedding model"
	@echo "  make embedserv-verify-model # Verify embedserv/models copy for the selected MLX embedding model"
	@echo "  make embedserv-serve   # Launch mlx-openai-server on LOCAL_EMBEDDINGS_URL"
	@echo ""
	@echo "Override env file: make up-lite ENV_FILE=.env.wrapper"
	@echo "Override Onyx tag: make onyx-build ONYX_IMAGE_TAG=v3.2.12"
	@echo "Override config ref: make upgrade-onyx ONYX_CONFIG_REF=main"
	@echo "Override install-time low-port remap: make up-full ONYX_INSTALL_HOST_PORT_80=3001"
	@echo "Override container engine: make up-lite CONTAINER_BIN=/opt/homebrew/bin/podman"
	@echo "Override Myst image: make myst-build MYST_IMAGE=local/myst:docker_host_fixes_with_logs"
	@echo "Override teep image: make teep-build TEEP_IMAGE=local/teep:main"
	@echo "Override embedding model: make embedserv-install MLX_EMBEDDING_MODEL=majentik/harrier-oss-v1-0.6b-MLX-8bit"

upgrade: myst-build teep-build searxng-image-ready tailscale-image-ready upgrade-onyx

tailscale-image-ready:
	@echo "Pulling Tailscale image: $(TAILSCALE_IMAGE)"; \
	"$(CONTAINER_BIN)" pull "$(TAILSCALE_IMAGE)"

up-lite: ONYX_INSTALL_ARGS=--lite
up-lite: ONYX_REQUIRED_IMAGES=$(ONYX_BACKEND_IMAGE) $(ONYX_WEB_SERVER_IMAGE)
up-lite: ensure-onyx-config sync-onyx-env onyx-image-ready myst-image-ready teep-image-ready
	@COMPOSE_FILE=$(LITE_FILES) "$(CONTAINER_BIN)" compose --env-file $(ENV_FILE) up -d --wait

up-full: ONYX_INSTALL_ARGS=
up-full: ONYX_REQUIRED_IMAGES=$(ONYX_BACKEND_IMAGE) $(ONYX_WEB_SERVER_IMAGE) $(ONYX_MODEL_SERVER_IMAGE)
up-full: ensure-onyx-config sync-onyx-env onyx-image-ready myst-image-ready teep-image-ready
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose --env-file $(ENV_FILE) up -d --wait

ensure-onyx-config:
	@set -eu; \
	if [ ! -f "$(ONYX_ENV_FILE)" ]; then \
		echo "$(ONYX_ENV_FILE) missing; downloading deployment files and initializing with install.sh"; \
		$(MAKE) upgrade-onyx ONYX_CONFIG_REF="$(ONYX_IMAGE_TAG)"; \
		exit 0; \
	fi; \
	current_tag=$$(sed -n 's/^IMAGE_TAG=//p' "$(ONYX_ENV_FILE)" | head -1); \
	if [ -z "$$current_tag" ]; then \
		echo "IMAGE_TAG missing in $(ONYX_ENV_FILE); running upgrade-onyx for $(ONYX_IMAGE_TAG)"; \
		$(MAKE) upgrade-onyx ONYX_CONFIG_REF="$(ONYX_IMAGE_TAG)"; \
	elif [ "$$current_tag" != "$(ONYX_IMAGE_TAG)" ]; then \
		echo "IMAGE_TAG mismatch ($$current_tag != $(ONYX_IMAGE_TAG)); running upgrade-onyx"; \
		$(MAKE) upgrade-onyx ONYX_CONFIG_REF="$(ONYX_IMAGE_TAG)"; \
	else \
		echo "Onyx deployment files already match ONYX_IMAGE_TAG=$(ONYX_IMAGE_TAG)"; \
	fi

init-onyx-env:
	@if [ -f "$(ONYX_ENV_FILE)" ]; then \
		echo "$(ONYX_ENV_FILE) already exists; skipping install.sh initialization"; \
		exit 0; \
	fi
	@echo "Running Onyx install script to initialize $(ONYX_ENV_FILE)..."
	@cd onyx && CONTAINER_BIN="$(CONTAINER_BIN)" ONYX_INSTALL_SCRIPT="$(ONYX_INSTALL_SCRIPT)" HOST_PORT_80="$(ONYX_INSTALL_HOST_PORT_80)" bash "$(ONYX_INSTALL_WRAPPER)" --no-prompt --local $(ONYX_INSTALL_ARGS)
	@cd onyx && CONTAINER_BIN="$(CONTAINER_BIN)" ONYX_INSTALL_SCRIPT="$(ONYX_INSTALL_SCRIPT)" HOST_PORT_80="$(ONYX_INSTALL_HOST_PORT_80)" bash "$(ONYX_INSTALL_WRAPPER)" --shutdown $(ONYX_INSTALL_ARGS) >/dev/null 2>&1 || true

sync-onyx-env:
	@if [ ! -f "$(ONYX_ENV_FILE)" ]; then \
		echo "ERROR: missing $(ONYX_ENV_FILE)"; \
		exit 1; \
	fi
	@if grep -q '^IMAGE_TAG=' "$(ONYX_ENV_FILE)"; then \
		sed -i.bak "s|^IMAGE_TAG=.*|IMAGE_TAG=$(ONYX_IMAGE_TAG)|" "$(ONYX_ENV_FILE)"; \
	else \
		printf '\nIMAGE_TAG=%s\n' "$(ONYX_IMAGE_TAG)" >> "$(ONYX_ENV_FILE)"; \
	fi
	@set -eu; \
	secret_raw=$$(sed -n 's/^USER_AUTH_SECRET=//p' "$(ONYX_ENV_FILE)" | head -1 || true); \
	secret_trimmed=$$(printf '%s' "$$secret_raw" | sed 's/^"//; s/"$$//'); \
	if [ -z "$$secret_trimmed" ]; then \
		if ! command -v openssl >/dev/null 2>&1; then \
			echo "ERROR: USER_AUTH_SECRET is unset and openssl was not found"; \
			exit 1; \
		fi; \
		new_secret=$$(openssl rand -hex 32); \
		if grep -q '^USER_AUTH_SECRET=' "$(ONYX_ENV_FILE)"; then \
			sed -i.bak "s|^USER_AUTH_SECRET=.*|USER_AUTH_SECRET=\"$$new_secret\"|" "$(ONYX_ENV_FILE)"; \
		else \
			printf '\nUSER_AUTH_SECRET="%s"\n' "$$new_secret" >> "$(ONYX_ENV_FILE)"; \
		fi; \
		echo "Generated USER_AUTH_SECRET in $(ONYX_ENV_FILE)"; \
	fi
	@echo "Synced $(ONYX_ENV_FILE): IMAGE_TAG=$(ONYX_IMAGE_TAG)"

upgrade-onyx:
	@set -eu; \
	config_ref="$(ONYX_CONFIG_REF)"; \
	if [ -z "$$config_ref" ]; then \
		echo "ERROR: could not determine Onyx config ref"; \
		exit 1; \
	fi; \
	compose_base="https://raw.githubusercontent.com/onyx-dot-app/onyx/$$config_ref/deployment/docker_compose"; \
	nginx_base="https://raw.githubusercontent.com/onyx-dot-app/onyx/$$config_ref/deployment/data/nginx"; \
	tmp_dir=$$(mktemp -d); \
	trap 'rm -rf "$$tmp_dir"' EXIT; \
	mkdir -p onyx/onyx_data/deployment onyx/onyx_data/data/nginx/local; \
	echo "Downloading Onyx deployment files for ref $$config_ref..."; \
	curl -fsSL "$$compose_base/docker-compose.yml" -o "$$tmp_dir/docker-compose.yml"; \
	curl -fsSL "$$compose_base/docker-compose.onyx-lite.yml" -o "$$tmp_dir/docker-compose.onyx-lite.yml"; \
	if curl -fsSL "$$compose_base/docker-compose.craft.yml" -o "$$tmp_dir/docker-compose.craft.yml" 2>/dev/null; then \
		install -m 0644 "$$tmp_dir/docker-compose.craft.yml" onyx/onyx_data/deployment/docker-compose.craft.yml; \
	else \
		echo "No docker-compose.craft.yml at ref $$config_ref; keeping existing local file if present"; \
	fi; \
	curl -fsSL "$$compose_base/env.template" -o "$$tmp_dir/env.template"; \
	curl -fsSL "$$compose_base/README.md" -o "$$tmp_dir/README.md"; \
	curl -fsSL "$$nginx_base/app.conf.template" -o "$$tmp_dir/app.conf.template"; \
	curl -fsSL "$$nginx_base/run-nginx.sh" -o "$$tmp_dir/run-nginx.sh"; \
	install -m 0644 "$$tmp_dir/docker-compose.yml" onyx/onyx_data/deployment/docker-compose.yml; \
	install -m 0644 "$$tmp_dir/docker-compose.onyx-lite.yml" onyx/onyx_data/deployment/docker-compose.onyx-lite.yml; \
	install -m 0644 "$$tmp_dir/env.template" onyx/onyx_data/deployment/env.template; \
	install -m 0644 "$$tmp_dir/README.md" onyx/onyx_data/README.md; \
	install -m 0644 "$$tmp_dir/app.conf.template" onyx/onyx_data/data/nginx/app.conf.template; \
	install -m 0755 "$$tmp_dir/run-nginx.sh" onyx/onyx_data/data/nginx/run-nginx.sh; \
	echo "Downloaded Onyx deployment files for ref $$config_ref"
	@if [ ! -f "$(ONYX_ENV_FILE)" ]; then \
		$(MAKE) init-onyx-env ONYX_INSTALL_ARGS="$(ONYX_INSTALL_ARGS)"; \
	fi
	@$(MAKE) sync-onyx-env

searxng-image-ready:
	@set -eu; \
	image=$$("$(CONTAINER_BIN)" compose --env-file "$(SEARXNG_ENV_FILE)" -f "$(SEARXNG_COMPOSE_FILE)" config | sed -n 's/^    image: //p' | head -1); \
	if [ -z "$$image" ]; then \
		echo "ERROR: could not resolve SearxNG image from $(SEARXNG_COMPOSE_FILE)"; \
		exit 1; \
	fi; \
	echo "Pulling SearxNG image: $$image"; \
	"$(CONTAINER_BIN)" pull "$$image"

down-lite:
	@COMPOSE_FILE=$(LITE_FILES) "$(CONTAINER_BIN)" compose --env-file $(ENV_FILE) down --remove-orphans

down-full:
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose --env-file $(ENV_FILE) down --remove-orphans

ps-lite:
	@COMPOSE_FILE=$(LITE_FILES) "$(CONTAINER_BIN)" compose --env-file $(ENV_FILE) ps

ps-full:
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose --env-file $(ENV_FILE) ps

logs-lite:
	@COMPOSE_FILE=$(LITE_FILES) "$(CONTAINER_BIN)" compose --env-file $(ENV_FILE) logs -f --tail=200

logs-full:
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose --env-file $(ENV_FILE) logs -f --tail=200

onyx-image-ready:
	@set -eu; \
	missing=0; \
	for image in $(ONYX_REQUIRED_IMAGES); do \
		if "$(CONTAINER_BIN)" image inspect "$$image" >/dev/null 2>&1; then \
			echo "Onyx image already present: $$image"; \
		else \
			echo "Onyx image missing: $$image"; \
			missing=1; \
		fi; \
	done; \
	if [ "$$missing" -eq 1 ]; then \
		echo "Missing Onyx image(s) detected. Running onyx-build..."; \
		$(MAKE) onyx-build ONYX_INSTALL_ARGS="$(ONYX_INSTALL_ARGS)" ONYX_REQUIRED_IMAGES="$(ONYX_REQUIRED_IMAGES)" ONYX_INSTALL_SCRIPT="$(ONYX_INSTALL_SCRIPT)"; \
	fi; \
	for image in $(ONYX_REQUIRED_IMAGES); do \
		"$(CONTAINER_BIN)" image inspect "$$image" >/dev/null 2>&1 || { echo "ERROR: Onyx image still missing after onyx-build: $$image"; exit 1; }; \
	done

onyx-build:
	@if [ -f "$(ONYX_ENV_FILE)" ]; then \
		if grep -q '^IMAGE_TAG=' "$(ONYX_ENV_FILE)"; then \
			sed -i.bak "s|^IMAGE_TAG=.*|IMAGE_TAG=$(ONYX_IMAGE_TAG)|" "$(ONYX_ENV_FILE)"; \
		else \
			printf '\nIMAGE_TAG=%s\n' "$(ONYX_IMAGE_TAG)" >> "$(ONYX_ENV_FILE)"; \
		fi; \
		echo "Updated $(ONYX_ENV_FILE): IMAGE_TAG=$(ONYX_IMAGE_TAG)"; \
	fi
	@echo "Running Onyx install script to prepare images (args: $(ONYX_INSTALL_ARGS))..."
	@cd onyx && CONTAINER_BIN="$(CONTAINER_BIN)" ONYX_INSTALL_SCRIPT="$(ONYX_INSTALL_SCRIPT)" HOST_PORT_80="$(ONYX_INSTALL_HOST_PORT_80)" bash "$(ONYX_INSTALL_WRAPPER)" --no-prompt $(ONYX_INSTALL_ARGS)
	@set -eu; \
	for image in $(ONYX_REQUIRED_IMAGES); do \
		echo "Ensuring Onyx image tag is present: $$image"; \
		"$(CONTAINER_BIN)" pull "$$image"; \
	done
	@cd onyx && CONTAINER_BIN="$(CONTAINER_BIN)" ONYX_INSTALL_SCRIPT="$(ONYX_INSTALL_SCRIPT)" HOST_PORT_80="$(ONYX_INSTALL_HOST_PORT_80)" bash "$(ONYX_INSTALL_WRAPPER)" --shutdown $(ONYX_INSTALL_ARGS) >/dev/null 2>&1 || true

myst-image-ready:
	@if "$(CONTAINER_BIN)" image inspect "$(MYST_IMAGE)" >/dev/null 2>&1; then \
		echo "Myst image already present: $(MYST_IMAGE)"; \
	else \
		echo "Myst image not found: $(MYST_IMAGE). Building..."; \
		$(MAKE) myst-build MYST_IMAGE="$(MYST_IMAGE)" MYST_NODE_REPO="$(MYST_NODE_REPO)" MYST_NODE_BRANCH="$(MYST_NODE_BRANCH)" MYST_DOCKERFILE="$(MYST_DOCKERFILE)"; \
	fi

myst-build:
	@echo "Building $(MYST_IMAGE) using $(MYST_DOCKERFILE) (repo=$(MYST_NODE_REPO), branch=$(MYST_NODE_BRANCH))..."
	@"$(CONTAINER_BIN)" build \
		--file "$(MYST_DOCKERFILE)" \
		--build-arg MYST_NODE_REPO="$(MYST_NODE_REPO)" \
		--build-arg MYST_NODE_BRANCH="$(MYST_NODE_BRANCH)" \
		--tag "$(MYST_IMAGE)" \
		.

teep-image-ready:
	@if "$(CONTAINER_BIN)" image inspect "$(TEEP_IMAGE)" >/dev/null 2>&1; then \
		echo "teep image already present: $(TEEP_IMAGE)"; \
	else \
		echo "teep image not found: $(TEEP_IMAGE). Building..."; \
		$(MAKE) teep-build TEEP_IMAGE="$(TEEP_IMAGE)" TEEP_REPO="$(TEEP_REPO)" TEEP_REF="$(TEEP_REF)" TEEP_DOCKERFILE="$(TEEP_DOCKERFILE)"; \
	fi

teep-build:
	@echo "Building $(TEEP_IMAGE) using $(TEEP_DOCKERFILE) (repo=$(TEEP_REPO), ref=$(TEEP_REF))..."
	@"$(CONTAINER_BIN)" build \
		--file "$(TEEP_DOCKERFILE)" \
		--build-arg TEEP_REPO="$(TEEP_REPO)" \
		--build-arg TEEP_REF="$(TEEP_REF)" \
		--tag "$(TEEP_IMAGE)" \
		.

embedserv-install:
	@set -eu; \
	if [ ! -f "$(ENV_FILE)" ]; then \
		echo "ERROR: missing $(ENV_FILE)"; \
		exit 1; \
	fi; \
	if ! command -v uv >/dev/null 2>&1; then \
		echo "ERROR: uv is required for embedserv-install"; \
		exit 1; \
	fi; \
	set -a; . "$(ENV_FILE)"; set +a; \
	model_repo="$${MLX_EMBEDDING_MODEL:-majentik/harrier-oss-v1-0.6b-MLX-8bit}"; \
	venv_python="$(PWD)/$(EMBEDSERV_VENV)/bin/python"; \
	model_dir="$(PWD)/$(EMBEDSERV_MODEL_CACHE)/$$model_repo"; \
	mkdir -p "$(EMBEDSERV_DIR)" "$$(dirname "$$model_dir")"; \
	if [ ! -x "$$venv_python" ]; then \
		uv venv --python 3.12 "$(EMBEDSERV_VENV)"; \
	fi; \
	uv pip install --python "$$venv_python" -r "$(EMBEDSERV_REQUIREMENTS)"; \
	echo "Downloading MLX embedding model: $$model_repo"; \
	MODEL_REPO="$$model_repo" MODEL_DIR="$$model_dir" "$$venv_python" -c 'import os; from huggingface_hub import snapshot_download; snapshot_download(repo_id=os.environ["MODEL_REPO"], local_dir=os.environ["MODEL_DIR"])'; \
	echo "Model ready at $$model_dir"

embedserv-verify-model: embedserv-install
	@set -eu; \
	if [ ! -f "$(ENV_FILE)" ]; then \
		echo "ERROR: missing $(ENV_FILE)"; \
		exit 1; \
	fi; \
	set -a; . "$(ENV_FILE)"; set +a; \
	model_repo="$${MLX_EMBEDDING_MODEL:-majentik/harrier-oss-v1-0.6b-MLX-8bit}"; \
	model_dir="$(PWD)/$(EMBEDSERV_MODEL_CACHE)/$$model_repo"; \
	if [ ! -d "$$model_dir" ]; then \
		echo "ERROR: model directory missing: $$model_dir"; \
		exit 1; \
	fi; \
	echo "Verifying served model directory: $$model_dir"; \
	verify_out=$$("$(PWD)/$(EMBEDSERV_VENV)/bin/hf" cache verify "$$model_repo" \
		--local-dir "$$model_dir" \
		--fail-on-missing-files 2>&1 || true); \
	printf '%s\n' "$$verify_out"; \
	if ! printf '%s\n' "$$verify_out" | grep -q "All checksums match."; then \
		echo "ERROR: local embedserv model verification failed"; \
		exit 1; \
	fi

embedserv-serve: embedserv-verify-model
	@set -eu; \
	if [ ! -f "$(ENV_FILE)" ]; then \
		echo "ERROR: missing $(ENV_FILE)"; \
		exit 1; \
	fi; \
	set -a; . "$(ENV_FILE)"; set +a; \
	model_repo="$${MLX_EMBEDDING_MODEL:-majentik/harrier-oss-v1-0.6b-MLX-8bit}"; \
	served_model="$${LOCAL_EMBEDDING_MODEL:-$$model_repo}"; \
	embeddings_url="$${LOCAL_EMBEDDINGS_URL:-http://host.docker.internal:1234/v1/embeddings}"; \
	model_dir="$(PWD)/$(EMBEDSERV_MODEL_CACHE)/$$model_repo"; \
	parsed_url=$$(URL="$$embeddings_url" python3 -c 'from urllib.parse import urlparse; import os; parsed = urlparse(os.environ["URL"]); print(parsed.scheme); print(parsed.hostname or ""); print("" if parsed.port is None else parsed.port); print(parsed.path.rstrip("/"))'); \
	scheme=$$(printf '%s\n' "$$parsed_url" | sed -n '1p'); \
	host=$$(printf '%s\n' "$$parsed_url" | sed -n '2p'); \
	port=$$(printf '%s\n' "$$parsed_url" | sed -n '3p'); \
	path=$$(printf '%s\n' "$$parsed_url" | sed -n '4p'); \
	if [ "$$scheme" != "http" ]; then \
		echo "ERROR: LOCAL_EMBEDDINGS_URL must use http: $$embeddings_url"; \
		exit 1; \
	fi; \
	if [ -z "$$host" ] || [ -z "$$port" ]; then \
		echo "ERROR: LOCAL_EMBEDDINGS_URL must include a host and explicit port: $$embeddings_url"; \
		exit 1; \
	fi; \
	if [ "$$path" != "/v1/embeddings" ]; then \
		echo "ERROR: LOCAL_EMBEDDINGS_URL must end with /v1/embeddings: $$embeddings_url"; \
		exit 1; \
	fi; \
	if [ "$$host" = "host.docker.internal" ]; then \
		host="0.0.0.0"; \
	fi; \
	echo "Launching mlx-openai-server for $$served_model on $$host:$$port (source URL: $$embeddings_url)"; \
	exec "$(PWD)/$(EMBEDSERV_VENV)/bin/mlx-openai-server" launch \
		--model-type embeddings \
		--model-path "$$model_dir" \
		--served-model-name "$$served_model" \
		--host "$$host" \
		--port "$$port"
