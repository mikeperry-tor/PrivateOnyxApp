ENV_FILE ?= .env.wrapper
VERSION_FILE ?= stack.versions.env
WRAPPER_FILE := docker-compose.yaml
FULL_OVERRIDE_FILE := docker-compose.full.yml
LITE_OVERRIDE_FILE := docker-compose.lite.yml
PODMAN_OVERRIDE_FILE := docker-compose.podman.yml
PODMAN_FULL_OVERRIDE_FILE := docker-compose.podman-full.yml
PODMAN_VPN_OVERRIDE_FILE := docker-compose.podman-vpn.yml
VPN_AUTOHEAL_OVERRIDE_FILE := docker-compose.vpn-autoheal.yml

env_value = $(strip $(shell for f in "$(ENV_FILE)" "$(VERSION_FILE)"; do [ -f "$$f" ] || continue; sed -n 's/^$(1)=//p' "$$f" | head -1 | sed 's/^"//; s/"$$//'; done | head -1))
COMPOSE_ENV_FILES = --env-file "$(VERSION_FILE)" --env-file "$(ENV_FILE)"
ONYX_COMPOSE_ENV_FILES = $(COMPOSE_ENV_FILES) --env-file "$(ONYX_ENV_FILE)"

MYST_NODE_REPO ?= https://github.com/mikeperry-tor/node.git
MYST_NODE_REF ?= $(call env_value,MYST_NODE_REF)
ifeq ($(strip $(MYST_NODE_REF)),)
$(error MYST_NODE_REF is not set in $(VERSION_FILE))
endif
MYST_DOCKERFILE ?= myst/build/Dockerfile
MYST_IMAGE ?= $(call env_value,MYST_IMAGE)
ifeq ($(strip $(MYST_IMAGE)),)
MYST_IMAGE := local/private-onyx-myst:$(shell printf '%s' '$(MYST_NODE_REF)' | cut -c1-12)
endif

TEEP_REPO ?= https://github.com/13rac1/teep.git
TEEP_DEFAULT_REF := 6413fe0547b449e67f7296986fe8b8ffbc9bbcd2
TEEP_REF ?= $(call env_value,TEEP_REF)
ifeq ($(strip $(TEEP_REF)),)
TEEP_REF := $(TEEP_DEFAULT_REF)
endif
TEEP_DOCKERFILE ?= teep/build/Dockerfile
TEEP_IMAGE ?= $(call env_value,TEEP_IMAGE)
ifeq ($(strip $(TEEP_IMAGE)),)
TEEP_IMAGE := 13rac1/teep:$(TEEP_REF)
endif
TAILSCALE_IMAGE ?= $(call env_value,TAILSCALE_IMAGE)
ifeq ($(strip $(TAILSCALE_IMAGE)),)
$(error TAILSCALE_IMAGE is not set in $(VERSION_FILE))
endif
PYTHON_SLIM_IMAGE ?= $(call env_value,PYTHON_SLIM_IMAGE)
PYTHON_ALPINE_IMAGE ?= $(call env_value,PYTHON_ALPINE_IMAGE)
OBSCURA_IMAGE ?= $(call env_value,OBSCURA_IMAGE)
ifeq ($(strip $(OBSCURA_IMAGE)),)
OBSCURA_IMAGE := h4ckf0r0day/obscura:0.1.10
endif
CONTAINER_BIN ?= $(call env_value,CONTAINER_BIN)
DOCKER_SOCK_PATH ?= $(call env_value,DOCKER_SOCK_PATH)
TEEP_ROUTE_THROUGH_MYST_VPN ?= $(call env_value,TEEP_ROUTE_THROUGH_MYST_VPN)
TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN ?= $(call env_value,TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN)
TAILSCALE_FUNNEL_ENABLED ?= $(call env_value,TAILSCALE_FUNNEL_ENABLED)
ONYX_CODE_INTERPRETER_ENABLE_NETWORK ?= $(call env_value,ONYX_CODE_INTERPRETER_ENABLE_NETWORK)
MYST_VPN_ENABLED ?= $(call env_value,MYST_VPN_ENABLED)
EGRESS_UPSTREAM_PROXY_URL ?= $(call env_value,EGRESS_UPSTREAM_PROXY_URL)
PODMAN_COMPOSE_PROVIDER ?= podman
ifeq ($(strip $(CONTAINER_BIN)),)
CONTAINER_BIN := docker
endif
PODMAN_SELECTED := $(if $(findstring podman,$(notdir $(CONTAINER_BIN))),true,false)
SHARED_DATA_ENGINE := $(if $(filter true,$(PODMAN_SELECTED)),podman,docker)
SHARED_DATA_ENGINE_MARKER := docker-data/host-services/shared-data-engine
ifeq ($(strip $(DOCKER_SOCK_PATH)),)
ifneq ($(findstring podman,$(CONTAINER_BIN)),)
DOCKER_SOCK_PATH := $(strip $(shell "$(CONTAINER_BIN)" machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}' 2>/dev/null | head -1))
endif
endif
export CONTAINER_BIN
export DOCKER_SOCK_PATH
ifeq ($(PODMAN_SELECTED),true)
# Docker Compose 5.3.x may otherwise honor the host's selected Docker context
# or Podman's SSH system connection. Pin the external provider to the current
# machine's forwarded compatibility socket; native engine commands still use
# CONTAINER_BIN=podman.
export DOCKER_HOST := unix://$(DOCKER_SOCK_PATH)
endif
export TEEP_IMAGE

ifneq ($(filter true,$(TAILSCALE_FUNNEL_ENABLED)),)
export COMPOSE_PROFILES := tailscale
endif

PODMAN_COMPOSE_SUFFIX :=
PODMAN_FULL_COMPOSE_SUFFIX :=
PODMAN_VPN_COMPOSE_SUFFIX :=
ifneq ($(findstring podman,$(CONTAINER_BIN)),)
PODMAN_COMPOSE_SUFFIX :=:$(PODMAN_OVERRIDE_FILE)
PODMAN_FULL_COMPOSE_SUFFIX :=:$(PODMAN_FULL_OVERRIDE_FILE)
endif

# Autoheal has Docker-socket authority and is useful only for recovering the
# VPN client. Keep it structurally absent from explicit no-VPN stacks.
VPN_AUTOHEAL_SUFFIX :=:$(VPN_AUTOHEAL_OVERRIDE_FILE)
ifneq ($(filter false,$(MYST_VPN_ENABLED)),)
VPN_AUTOHEAL_SUFFIX :=
endif
ifneq ($(findstring podman,$(CONTAINER_BIN)),)
ifneq ($(strip $(VPN_AUTOHEAL_SUFFIX)),)
PODMAN_VPN_COMPOSE_SUFFIX :=:$(PODMAN_VPN_OVERRIDE_FILE)
endif
endif

# Conditional routing overrides for teep and tailscale.
# When true, a fixed Teep gateway joins the trusted routing namespace; the
# Onyx caller and Teep service remain on explicit internal networks.
TEEP_VPN_SUFFIX :=
ifneq ($(filter true,$(TEEP_ROUTE_THROUGH_MYST_VPN)),)
TEEP_VPN_SUFFIX :=:docker-compose.teep-vpn.yml
endif

# When true, Tailscale shares the trusted route namespace but still reaches
# Onyx only through its fixed frontend gateway.
TAILSCALE_VPN_SUFFIX :=
ifneq ($(filter true,$(TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN)),)
TAILSCALE_VPN_SUFFIX :=:docker-compose.tailscale-vpn.yml
endif

# When enabled, executor pods join only the named internal executor network.
CODE_INTERPRETER_NETWORK_SUFFIX :=
ifneq ($(filter true,$(ONYX_CODE_INTERPRETER_ENABLE_NETWORK)),)
ifneq ($(PODMAN_SELECTED),true)
CODE_INTERPRETER_NETWORK_SUFFIX :=:docker-compose.code-interpreter-network.yml
endif
endif

# When EGRESS_UPSTREAM_PROXY_URL is non-empty, apply the proxy override
# layer records the explicit upstream-proxy mode. Restricted components always
# use local bridges; only final-hop policy proxies receive the upstream URL.
PROXY_SUFFIX :=
ifneq ($(strip $(EGRESS_UPSTREAM_PROXY_URL)),)
PROXY_SUFFIX :=:docker-compose.proxy.yml
endif

# Source of truth: ONYX_IMAGE_TAG in $(VERSION_FILE). Allow ENV_FILE or CLI override.
ONYX_IMAGE_TAG ?= $(call env_value,ONYX_IMAGE_TAG)
ifeq ($(strip $(ONYX_IMAGE_TAG)),)
$(error ONYX_IMAGE_TAG is not set. Add ONYX_IMAGE_TAG=... to $(VERSION_FILE), override it in $(ENV_FILE), or pass ONYX_IMAGE_TAG=... on the make command line)
endif
ONYX_BACKEND_IMAGE ?= onyxdotapp/onyx-backend:$(ONYX_IMAGE_TAG)
ONYX_WEB_SERVER_IMAGE ?= onyxdotapp/onyx-web-server:$(ONYX_IMAGE_TAG)
SEARXNG_IMAGE_TAG ?= $(call env_value,SEARXNG_IMAGE_TAG)
ifeq ($(strip $(SEARXNG_IMAGE_TAG)),)
$(error SEARXNG_IMAGE_TAG is not set. Add SEARXNG_IMAGE_TAG=... to $(VERSION_FILE), override it in $(ENV_FILE), or pass SEARXNG_IMAGE_TAG=... on the make command line)
endif
SEARXNG_IMAGE ?= docker.io/searxng/searxng:$(SEARXNG_IMAGE_TAG)
SEARXNG_DOCKERFILE ?= searxng/Dockerfile
SEARXNG_WRAPPER_IMAGE_REPOSITORY ?= $(call env_value,SEARXNG_WRAPPER_IMAGE_REPOSITORY)
ifeq ($(strip $(SEARXNG_WRAPPER_IMAGE_REPOSITORY)),)
$(error SEARXNG_WRAPPER_IMAGE_REPOSITORY is not set. Add it to $(VERSION_FILE) or override it on the make command line)
endif
SEARXNG_WRAPPER_BUILD_INPUTS := \
	$(SEARXNG_DOCKERFILE) \
	searxng/requirements.txt \
	$(sort $(wildcard browser/obscura_client/private_onyx_obscura/*.py)) \
	$(sort $(wildcard searxng/engines/*.py))
SEARXNG_WRAPPER_SOURCE_HASH := $(shell python3 -c 'import hashlib,pathlib,sys; h=hashlib.sha256(); [h.update(p.encode()+b"\0"+pathlib.Path(p).read_bytes()) for p in sys.argv[1:]]; print(h.hexdigest()[:12])' $(SEARXNG_WRAPPER_BUILD_INPUTS))
ifeq ($(strip $(SEARXNG_WRAPPER_SOURCE_HASH)),)
$(error could not compute the SearXNG wrapper source hash)
endif
# Couple the local tag to both the upstream pin and every source copied into
# the derived image. `make up-*` therefore cannot reuse an older image after a
# client, engine, dependency-lock, or Dockerfile change. A command-line
# SEARXNG_IMAGE_TAG override also selects a distinct derived tag.
SEARXNG_WRAPPER_IMAGE ?= $(SEARXNG_WRAPPER_IMAGE_REPOSITORY):$(SEARXNG_IMAGE_TAG)-$(SEARXNG_WRAPPER_SOURCE_HASH)
ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB ?= $(call env_value,ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB)
ifeq ($(strip $(ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB)),)
ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB := 20
endif
ONYX_RAG_DOC_SOURCE_DIR ?= $(call env_value,ONYX_RAG_DOC_SOURCE_DIR)
ifeq ($(strip $(ONYX_RAG_DOC_SOURCE_DIR)),)
ONYX_RAG_DOC_SOURCE_DIR := ./doc-drop
endif
PODMAN_DOC_SERVER_PORT := 18091
PODMAN_DOC_SERVER_STATE_DIR := docker-data/host-services
PODMAN_DOC_SERVER_PID_FILE := $(PODMAN_DOC_SERVER_STATE_DIR)/podman-doc-server.pid
PODMAN_DOC_SERVER_LOG := $(PODMAN_DOC_SERVER_STATE_DIR)/podman-doc-server.log
export PODMAN_DOC_SERVER_PORT
# The shared Obscura server must accommodate both the configurable built-in
# open_url document limit and SearXNG's independent fixed 20 MiB DOM limit.
OBSCURA_RETENTION_FLOOR_BYTES := $(shell python3 -c 'v=int("$(ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB)"); assert 0 < v <= ((1<<63)-1)//1048576; print(max(v*1048576,20971520))')
OBSCURA_IO_STREAM_MAX_BYTES := $(shell python3 -c 'v=int("$(OBSCURA_RETENTION_FLOOR_BYTES)"); assert v <= ((1<<63)-1)//5; print(v*5)')
export SEARXNG_WRAPPER_IMAGE
export SEARXNG_WRAPPER_IMAGE_REPOSITORY
export SEARXNG_WRAPPER_SOURCE_HASH
export OBSCURA_RETENTION_FLOOR_BYTES
export OBSCURA_IO_STREAM_MAX_BYTES
export SEARXNG_IMAGE_TAG
SEARXNG_SECRET := $(strip $(shell openssl rand -hex 32 2>/dev/null))
USER_AUTH_SECRET := $(strip $(shell openssl rand -hex 32 2>/dev/null))
MINIO_ROOT_USER := $(strip $(shell openssl rand -hex 16 2>/dev/null))
MINIO_ROOT_PASSWORD := $(strip $(shell openssl rand -hex 32 2>/dev/null))
S3_AWS_ACCESS_KEY_ID := $(MINIO_ROOT_USER)
S3_AWS_SECRET_ACCESS_KEY := $(MINIO_ROOT_PASSWORD)
ifeq ($(strip $(SEARXNG_SECRET)$(USER_AUTH_SECRET)$(MINIO_ROOT_PASSWORD)),)
$(error openssl is required to generate ephemeral local stack secrets)
endif
export SEARXNG_SECRET
export USER_AUTH_SECRET
export MINIO_ROOT_USER
export MINIO_ROOT_PASSWORD
export S3_AWS_ACCESS_KEY_ID
export S3_AWS_SECRET_ACCESS_KEY
CODE_INTERPRETER_IMAGE_TAG ?= $(call env_value,CODE_INTERPRETER_IMAGE_TAG)
ifeq ($(strip $(CODE_INTERPRETER_IMAGE_TAG)),)
$(error CODE_INTERPRETER_IMAGE_TAG is not set. Add CODE_INTERPRETER_IMAGE_TAG=... to $(VERSION_FILE), override it in $(ENV_FILE), or pass CODE_INTERPRETER_IMAGE_TAG=... on the make command line)
endif
CODE_INTERPRETER_IMAGE ?= onyxdotapp/code-interpreter:$(CODE_INTERPRETER_IMAGE_TAG)
export CODE_INTERPRETER_IMAGE_TAG
ifeq ($(PODMAN_SELECTED),true)
ONYX_STACK_REQUIRED_IMAGES := $(ONYX_BACKEND_IMAGE) $(ONYX_WEB_SERVER_IMAGE)
else
ONYX_STACK_REQUIRED_IMAGES := $(ONYX_BACKEND_IMAGE) $(ONYX_WEB_SERVER_IMAGE) $(CODE_INTERPRETER_IMAGE)
endif
ONYX_INSTALL_SCRIPT ?= ./install.sh
ONYX_INSTALL_WRAPPER ?= ./install-with-container-bin.sh
ONYX_ENV_FILE ?= onyx/onyx_data/deployment/.env
ONYX_CONFIG_REF ?= $(ONYX_IMAGE_TAG)
ONYX_INSTALL_HOST_PORT_80 ?= 3001
SEARXNG_COMPOSE_FILE := searxng/docker-compose.yml
MYST_COMPOSE_FILE := myst/docker-compose.yaml
MYST_VPN_CLI := myst/myst-vpn-cli.sh
MYST_CONTAINER_NAME := myst-client-vpn
MYST_DATA_DIR := docker-data/myst-data
EMBEDSERV_DIR := embedserv
EMBEDSERV_REQUIREMENTS_IN := $(EMBEDSERV_DIR)/requirements.in
EMBEDSERV_REQUIREMENTS := $(EMBEDSERV_DIR)/requirements.txt
EMBEDSERV_VENV := $(EMBEDSERV_DIR)/.venv
EMBEDSERV_MODEL_CACHE := $(EMBEDSERV_DIR)/models
EMBEDSERV_DEFAULT_UPSTREAM_URL := http://host.docker.internal:3210/v1/embeddings
EMBEDSERV_LOG := $(EMBEDSERV_DIR)/serve.log
EMBEDSERV_PID_FILE := $(EMBEDSERV_DIR)/serve.pid
EMBEDSERV_CHILD_PID_FILE := $(EMBEDSERV_DIR)/child.pid
SEARXNG_REQUIREMENTS_IN := searxng/requirements.in
SEARXNG_REQUIREMENTS := searxng/requirements.txt
UV_CACHE_DIR ?= /tmp/private-onyx-uv-cache

LITE_FILES := $(WRAPPER_FILE):$(LITE_OVERRIDE_FILE)$(VPN_AUTOHEAL_SUFFIX)$(PODMAN_COMPOSE_SUFFIX)$(PODMAN_VPN_COMPOSE_SUFFIX)$(TEEP_VPN_SUFFIX)$(TAILSCALE_VPN_SUFFIX)$(CODE_INTERPRETER_NETWORK_SUFFIX)$(PROXY_SUFFIX)
FULL_FILES := $(WRAPPER_FILE):$(FULL_OVERRIDE_FILE)$(VPN_AUTOHEAL_SUFFIX)$(PODMAN_COMPOSE_SUFFIX)$(PODMAN_FULL_COMPOSE_SUFFIX)$(PODMAN_VPN_COMPOSE_SUFFIX)$(TEEP_VPN_SUFFIX)$(TAILSCALE_VPN_SUFFIX)$(CODE_INTERPRETER_NETWORK_SUFFIX)$(PROXY_SUFFIX)

.PHONY: help test check test-images check-upgrade health-inventory shared-data-engine-status claim-shared-data-engine release-shared-data-engine up-lite up-full down-lite down-full ps-lite ps-full logs-lite logs-full check-container-health-capability prepare-podman-postgres-data prepare-podman-opensearch-data podman-doc-server-start podman-doc-server-stop-if-started embedding-ready-once ensure-onyx-config init-onyx-env sync-onyx-env upgrade upgrade-onyx upgrade-python-deps searxng-image-ready searxng-build obscura-image-ready tailscale-image-ready myst-image-ready myst-build teep-image-ready teep-build onyx-image-ready onyx-build embedserv-install embedserv-verify-model embedserv-serve embedserv-start-if-installed embedserv-stop-if-started embedserv-cleanup-recorded-child vpn-signup-orderform vpn-signup-blockchain vpn-orderstatus vpn-balance ensure-myst-funded

.NOTPARALLEL: up-lite up-full

help:
	@echo "Targets:"
	@echo "  make test         # Run the deterministic Python test suite"
	@echo "  make check        # Run deterministic tests and local static checks"
	@echo "  make test-images  # Validate patches against already-built pinned images"
	@echo "  make check-upgrade # Run check plus pinned-image validation after an upgrade"
	@echo "  make health-inventory # Print effective lite/full health cadence inventory"
	@echo "  make shared-data-engine-status # Show the Docker/Podman shared-data owner"
	@echo "  make up-lite      # Start wrapper + Onyx lite"
	@echo "  make up-full      # Start wrapper + Onyx full and an installed default MLX server"
	@echo "  make down-lite    # Stop wrapper + Onyx lite"
	@echo "  make down-full    # Stop wrapper + Onyx full"
	@echo "  make ps-lite      # Show lite mode containers"
	@echo "  make ps-full      # Show full mode containers"
	@echo "  make logs-lite    # Tail lite mode logs"
	@echo "  make logs-full    # Tail full mode logs"
	@echo "  make upgrade      # Upgrade Python locks, rebuild Myst + teep, refresh Onyx deployment files, and pull companion images"
	@echo "  make embedserv-install # Create embedserv venv with uv and download the MLX embedding model"
	@echo "  make embedserv-verify-model # Verify embedserv/models copy for the selected MLX embedding model"
	@echo "  make embedserv-serve   # Launch mlx-openai-server on ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL"
	@echo ""
	@echo "VPN signup & payment:"
	@echo "  make vpn-signup-orderform  # Start standalone Myst container, create identity + CoinGate order, show payment URL"
	@echo "  make vpn-signup-blockchain # Start standalone Myst container, create identity, show channel address for direct MYST transfer"
	@echo "  make vpn-orderstatus       # Show balance, order status, and payment URL"
	@echo "  make vpn-balance           # Quick balance check"
	@echo ""
	@echo "Version manifest: $(VERSION_FILE) (override with VERSION_FILE=...)"
	@echo "Override env file: make up-lite ENV_FILE=.env.wrapper"
	@echo "Disable VPN: set MYST_VPN_ENABLED=false in $(ENV_FILE) to idle myst-client without kill-switch/connect"
	@echo "Proxy: set EGRESS_UPSTREAM_PROXY_URL in $(ENV_FILE) (http/https/socks5)"
	@echo "       to route Onyx helpers, Obscura, and network-enabled executor egress"

test:
	python3 -m unittest discover -s tests -p 'test_*.py' -v

check: test
	@$(MAKE) --no-print-directory help >/dev/null
	python3 -m compileall -q \
		browser/obscura_client/private_onyx_obscura \
		egress \
		onyx/patches \
		onyx/doc_drop_webserver.py \
		onyx/local_embedding_shim.py \
		onyx/background_entrypoint.py \
		onyx/beat_liveness_watchdog.py \
		embedserv/idle_embedding_proxy.py \
		podman \
		searxng/engines \
		searxng/patches \
		tests
	git diff --check

health-inventory:
	@COMPOSE_FILE=$(LITE_FILES) python3 tests/health_inventory.py lite --container-bin "$(CONTAINER_BIN)" $(ONYX_COMPOSE_ENV_FILES)
	@COMPOSE_FILE=$(FULL_FILES) python3 tests/health_inventory.py full --container-bin "$(CONTAINER_BIN)" $(ONYX_COMPOSE_ENV_FILES)

shared-data-engine-status:
	@python3 podman/shared_data_engine.py status --marker "$(SHARED_DATA_ENGINE_MARKER)"

claim-shared-data-engine:
	@python3 podman/shared_data_engine.py claim --engine "$(SHARED_DATA_ENGINE)" --marker "$(SHARED_DATA_ENGINE_MARKER)"

release-shared-data-engine:
	@python3 podman/shared_data_engine.py release --engine "$(SHARED_DATA_ENGINE)" --marker "$(SHARED_DATA_ENGINE_MARKER)"

test-images:
	@CONTAINER_BIN="$(CONTAINER_BIN)" \
		ONYX_BACKEND_IMAGE="$(ONYX_BACKEND_IMAGE)" \
		CODE_INTERPRETER_IMAGE="$(CODE_INTERPRETER_IMAGE)" \
		SEARXNG_WRAPPER_IMAGE="$(SEARXNG_WRAPPER_IMAGE)" \
		./tests/validate_pinned_patch_images.sh

check-upgrade:
	@$(MAKE) --no-print-directory check
	@$(MAKE) --no-print-directory test-images

upgrade: upgrade-python-deps myst-build teep-build searxng-build tailscale-image-ready obscura-image-ready upgrade-onyx
	@echo "Upgrade artifacts are ready. Run 'make check-upgrade', then complete the documented live validation matrix."

upgrade-python-deps:
	@set -eu; \
	if ! command -v uv >/dev/null 2>&1; then \
		echo "ERROR: uv is required for upgrade-python-deps"; \
		exit 1; \
	fi; \
	UV_CACHE_DIR="$(UV_CACHE_DIR)" uv pip compile --upgrade --generate-hashes "$(EMBEDSERV_REQUIREMENTS_IN)" -o "$(EMBEDSERV_REQUIREMENTS)"; \
	UV_CACHE_DIR="$(UV_CACHE_DIR)" uv pip compile --upgrade --generate-hashes "$(SEARXNG_REQUIREMENTS_IN)" -o "$(SEARXNG_REQUIREMENTS)"

tailscale-image-ready:
	@echo "Pulling Tailscale image: $(TAILSCALE_IMAGE)"; \
	"$(CONTAINER_BIN)" pull "$(TAILSCALE_IMAGE)"

obscura-image-ready:
	@echo "Pulling obscura image: $(OBSCURA_IMAGE)"; \
	"$(CONTAINER_BIN)" pull "$(OBSCURA_IMAGE)"

searxng-image-ready:
	@if "$(CONTAINER_BIN)" image inspect "$(SEARXNG_WRAPPER_IMAGE)" >/dev/null 2>&1; then \
		echo "SearXNG wrapper image already present: $(SEARXNG_WRAPPER_IMAGE)"; \
	else \
		$(MAKE) searxng-build; \
	fi

searxng-build:
	@echo "Building $(SEARXNG_WRAPPER_IMAGE) using $(SEARXNG_DOCKERFILE)..."
	@set -eu; set --; \
	[ -z "$${HTTP_PROXY:-}" ] || set -- "$$@" --build-arg HTTP_PROXY; \
	[ -z "$${HTTPS_PROXY:-}" ] || set -- "$$@" --build-arg HTTPS_PROXY; \
	[ -z "$${NO_PROXY:-}" ] || set -- "$$@" --build-arg NO_PROXY; \
	[ -z "$${http_proxy:-}" ] || set -- "$$@" --build-arg http_proxy; \
	[ -z "$${https_proxy:-}" ] || set -- "$$@" --build-arg https_proxy; \
	[ -z "$${no_proxy:-}" ] || set -- "$$@" --build-arg no_proxy; \
	"$(CONTAINER_BIN)" build "$$@" \
		--file "$(SEARXNG_DOCKERFILE)" \
		--build-arg SEARXNG_UPSTREAM_IMAGE="$(SEARXNG_IMAGE)" \
		--tag "$(SEARXNG_WRAPPER_IMAGE)" \
		.

up-lite: ONYX_INSTALL_ARGS=--lite
up-lite: ONYX_REQUIRED_IMAGES=$(ONYX_STACK_REQUIRED_IMAGES)
check-container-health-capability:
	@set -eu; \
	case "$(CONTAINER_BIN)" in \
		*podman*) \
			exec python3 podman/startup_health.py check --container-bin "$(CONTAINER_BIN)" \
			;; \
	esac; \
	engine_version="$$($(CONTAINER_BIN) version --format '{{.Server.Version}}')"; \
	compose_version="$$($(CONTAINER_BIN) compose version --short)"; \
	ENGINE_VERSION="$$engine_version" COMPOSE_VERSION="$$compose_version" python3 -c 'import os; from re import findall; parse=lambda v: tuple((list(map(int, findall(r"\d+", v)))+[0,0,0])[:3]); assert parse(os.environ["ENGINE_VERSION"]) >= (25,0,0), "Docker Engine 25.0+ is required for start_interval"; assert parse(os.environ["COMPOSE_VERSION"]) >= (2,20,2), "Docker Compose 2.20.2+ is required for start_interval"'

podman-doc-server-start:
ifeq ($(PODMAN_SELECTED),true)
	@set -eu; \
	source_dir="$(abspath $(ONYX_RAG_DOC_SOURCE_DIR))"; \
	pid_file="$(PODMAN_DOC_SERVER_PID_FILE)"; \
	log_file="$(PODMAN_DOC_SERVER_LOG)"; \
	server_script="$(CURDIR)/onyx/doc_drop_webserver.py"; \
	config_id=$$(python3 -c 'import hashlib,sys; print(hashlib.sha256("\0".join(sys.argv[1:]).encode()).hexdigest())' \
		"$$server_script" "$(PODMAN_DOC_SERVER_PORT)" "0.0.0.0" "$$source_dir" "loopback-peers-only"); \
	if [ ! -d "$$source_dir" ]; then \
		echo "ERROR: configured RAG document source is not a directory"; \
		exit 1; \
	fi; \
	mkdir -p "$(PODMAN_DOC_SERVER_STATE_DIR)"; \
	if [ -f "$$pid_file" ]; then \
		pid=$$(sed -n '1p' "$$pid_file"); \
		owner_token=$$(sed -n '2p' "$$pid_file"); \
		recorded_config_id=$$(sed -n '3p' "$$pid_file"); \
		case "$$pid" in ''|*[!0-9]*) pid="" ;; esac; \
		command_line=""; \
		if [ -n "$$pid" ] && current_command=$$(ps -p "$$pid" -o command= 2>/dev/null); then command_line="$$current_command"; fi; \
		owned=false; \
		if python3 -c 'import re,sys; raise SystemExit(0 if re.fullmatch(r"[0-9a-f]{64}", sys.argv[1]) else 1)' "$$owner_token"; then \
			case "$$command_line" in *"$$server_script "*"--owner-token $$owner_token"*) owned=true ;; esac; \
		fi; \
		if [ "$$owned" = true ] && [ "$$recorded_config_id" = "$$config_id" ]; then \
				if python3 -c 'import http.client; c=http.client.HTTPConnection("127.0.0.1", $(PODMAN_DOC_SERVER_PORT), timeout=1); c.request("GET", "/_health"); r=c.getresponse(); r.read(); c.close(); raise SystemExit(0 if r.status == 200 else 1)' >/dev/null 2>&1; then \
					echo "Podman host document server is already ready on port $(PODMAN_DOC_SERVER_PORT)"; \
					exit 0; \
				fi; \
				echo "ERROR: tracked Podman host document server is running but not ready; stop it with make down-full"; \
				exit 1; \
		fi; \
		if [ "$$owned" = true ]; then \
			echo "Restarting tracked Podman host document server after configuration changed"; \
			kill -TERM "$$pid"; \
			attempt=0; \
			while kill -0 "$$pid" 2>/dev/null && [ "$$attempt" -lt 100 ]; do sleep 0.1; attempt=$$((attempt + 1)); done; \
			if kill -0 "$$pid" 2>/dev/null; then echo "ERROR: prior Podman host document server did not stop"; exit 1; fi; \
		fi; \
		rm -f -- "$$pid_file"; \
	fi; \
	if python3 -c 'import socket; s=socket.create_connection(("127.0.0.1", $(PODMAN_DOC_SERVER_PORT)), timeout=1); s.close()' >/dev/null 2>&1; then \
		echo "ERROR: port $(PODMAN_DOC_SERVER_PORT) is already owned by an untracked process"; \
		exit 1; \
	fi; \
	echo "Starting loopback-peer-restricted Podman host document server on port $(PODMAN_DOC_SERVER_PORT)"; \
	owner_token=$$(python3 -c 'import secrets; print(secrets.token_hex(32))'); \
	pid=$$(python3 -c 'import subprocess,sys; log=open(sys.argv[1], "ab", buffering=0); child=subprocess.Popen(sys.argv[2:], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True); print(child.pid)' \
		"$$log_file" python3 "$$server_script" "$(PODMAN_DOC_SERVER_PORT)" --bind 0.0.0.0 --directory "$$source_dir" --loopback-peers-only --owner-token "$$owner_token"); \
	printf '%s\n%s\n%s\n' "$$pid" "$$owner_token" "$$config_id" >"$$pid_file"; \
	attempt=0; \
	while [ "$$attempt" -lt 40 ]; do \
		if python3 -c 'import http.client; c=http.client.HTTPConnection("127.0.0.1", $(PODMAN_DOC_SERVER_PORT), timeout=1); c.request("GET", "/_health"); r=c.getresponse(); r.read(); c.close(); raise SystemExit(0 if r.status == 200 else 1)' >/dev/null 2>&1; then \
			echo "Podman host document server is ready"; \
			exit 0; \
		fi; \
		if ! kill -0 "$$pid" 2>/dev/null; then \
			echo "ERROR: Podman host document server exited during startup; inspect $$log_file"; \
			rm -f -- "$$pid_file"; \
			exit 1; \
		fi; \
		sleep 0.25; \
		attempt=$$((attempt + 1)); \
	done; \
	echo "ERROR: Podman host document server did not become ready; inspect $$log_file"; \
	kill -TERM "$$pid"; \
	rm -f -- "$$pid_file"; \
	exit 1
endif

podman-doc-server-stop-if-started:
	@set -eu; \
	pid_file="$(PODMAN_DOC_SERVER_PID_FILE)"; \
	if [ ! -f "$$pid_file" ]; then \
		echo "No automatically started Podman host document server PID is recorded"; \
		exit 0; \
	fi; \
	pid=$$(sed -n '1p' "$$pid_file"); \
	owner_token=$$(sed -n '2p' "$$pid_file"); \
	case "$$pid" in \
		''|*[!0-9]*) \
			echo "Ignoring malformed Podman host document server PID file"; \
			rm -f -- "$$pid_file"; \
			exit 0 \
			;; \
	esac; \
	if ! python3 -c 'import re,sys; raise SystemExit(0 if re.fullmatch(r"[0-9a-f]{64}", sys.argv[1]) else 1)' "$$owner_token"; then \
		echo "Ignoring Podman host document server record with an invalid ownership token"; \
		rm -f -- "$$pid_file"; \
		exit 0; \
	fi; \
	command_line=""; \
	if current_command=$$(ps -p "$$pid" -o command= 2>/dev/null); then command_line="$$current_command"; fi; \
	if [ -z "$$command_line" ]; then \
		echo "Automatically started Podman host document server is no longer running"; \
		rm -f -- "$$pid_file"; \
		exit 0; \
	fi; \
	case "$$command_line" in \
		*'$(CURDIR)/onyx/doc_drop_webserver.py '*"--owner-token $$owner_token"*) ;; \
		*) \
			echo "PID $$pid no longer belongs to the Podman host document server; leaving it untouched"; \
			rm -f -- "$$pid_file"; \
			exit 0 \
			;; \
	esac; \
	echo "Stopping automatically started Podman host document server (PID $$pid)"; \
	kill -TERM "$$pid"; \
	attempt=0; \
	while kill -0 "$$pid" 2>/dev/null && [ "$$attempt" -lt 100 ]; do \
		sleep 0.1; \
		attempt=$$((attempt + 1)); \
	done; \
	if kill -0 "$$pid" 2>/dev/null; then \
		echo "ERROR: Podman host document server PID $$pid did not stop; inspect $(PODMAN_DOC_SERVER_LOG)"; \
		exit 1; \
	fi; \
	rm -f -- "$$pid_file"; \
	echo "Automatically started Podman host document server stopped"

prepare-podman-postgres-data:
ifeq ($(PODMAN_SELECTED),true)
	@python3 podman/startup_health.py prepare-shared-data --postgres docker-data/postgres
endif

prepare-podman-opensearch-data:
ifeq ($(PODMAN_SELECTED),true)
	@python3 podman/startup_health.py prepare-shared-data --opensearch docker-data/opensearch
endif

up-lite: claim-shared-data-engine ensure-onyx-config sync-onyx-env check-container-health-capability prepare-podman-postgres-data ensure-myst-funded onyx-image-ready myst-image-ready teep-image-ready searxng-image-ready obscura-image-ready
ifeq ($(PODMAN_SELECTED),true)
	@COMPOSE_FILE=$(LITE_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) create
	@COMPOSE_FILE=$(LITE_FILES) python3 podman/startup_health.py configure --container-bin "$(CONTAINER_BIN)" --project onyx $(ONYX_COMPOSE_ENV_FILES)
endif
	@COMPOSE_FILE=$(LITE_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) up -d --wait --wait-timeout 420

up-full: ONYX_INSTALL_ARGS=
up-full: ONYX_REQUIRED_IMAGES=$(ONYX_STACK_REQUIRED_IMAGES)
up-full: claim-shared-data-engine ensure-onyx-config sync-onyx-env check-container-health-capability prepare-podman-postgres-data prepare-podman-opensearch-data podman-doc-server-start ensure-myst-funded onyx-image-ready myst-image-ready teep-image-ready searxng-image-ready obscura-image-ready embedserv-start-if-installed
ifeq ($(PODMAN_SELECTED),true)
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) create local-embedding-shim
	@COMPOSE_FILE=$(FULL_FILES) python3 podman/startup_health.py configure --container-bin "$(CONTAINER_BIN)" --project onyx $(ONYX_COMPOSE_ENV_FILES)
endif
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) up -d --wait --wait-timeout 420 local-embedding-shim
	@$(MAKE) --no-print-directory embedding-ready-once
ifeq ($(PODMAN_SELECTED),true)
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) create
	@COMPOSE_FILE=$(FULL_FILES) python3 podman/startup_health.py configure --container-bin "$(CONTAINER_BIN)" --project onyx $(ONYX_COMPOSE_ENV_FILES)
endif
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) up -d --wait --wait-timeout 420

embedding-ready-once:
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) exec -T local-embedding-shim \
		wget -q -T 35 -O /dev/null http://127.0.0.1:9101/ready

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
	@cd onyx && CONTAINER_BIN="$(CONTAINER_BIN)" PODMAN_COMPOSE_PROVIDER="$(PODMAN_COMPOSE_PROVIDER)" ONYX_DESIRED_IMAGE_TAG="$(ONYX_IMAGE_TAG)" ONYX_INSTALL_SCRIPT="$(ONYX_INSTALL_SCRIPT)" HOST_PORT_80="$(ONYX_INSTALL_HOST_PORT_80)" bash "$(ONYX_INSTALL_WRAPPER)" --no-prompt --local $(ONYX_INSTALL_ARGS)
	@cd onyx && CONTAINER_BIN="$(CONTAINER_BIN)" PODMAN_COMPOSE_PROVIDER="$(PODMAN_COMPOSE_PROVIDER)" ONYX_DESIRED_IMAGE_TAG="$(ONYX_IMAGE_TAG)" ONYX_INSTALL_SCRIPT="$(ONYX_INSTALL_SCRIPT)" HOST_PORT_80="$(ONYX_INSTALL_HOST_PORT_80)" bash "$(ONYX_INSTALL_WRAPPER)" --shutdown $(ONYX_INSTALL_ARGS) >/dev/null 2>&1 || true

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
	@if grep -q '^CODE_INTERPRETER_IMAGE_TAG=' "$(ONYX_ENV_FILE)"; then \
		sed -i.bak "s|^CODE_INTERPRETER_IMAGE_TAG=.*|CODE_INTERPRETER_IMAGE_TAG=$(CODE_INTERPRETER_IMAGE_TAG)|" "$(ONYX_ENV_FILE)"; \
	else \
		printf '\nCODE_INTERPRETER_IMAGE_TAG=%s\n' "$(CODE_INTERPRETER_IMAGE_TAG)" >> "$(ONYX_ENV_FILE)"; \
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

down-lite:
	@COMPOSE_PROFILES=tailscale COMPOSE_FILE=$(LITE_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) down --remove-orphans
	@$(MAKE) --no-print-directory release-shared-data-engine

down-full:
	@COMPOSE_PROFILES=tailscale COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) down --remove-orphans
	@"$${MAKE:-make}" podman-doc-server-stop-if-started
	@"$${MAKE:-make}" embedserv-stop-if-started
	@"$${MAKE:-make}" embedserv-cleanup-recorded-child
	@$(MAKE) --no-print-directory release-shared-data-engine

ps-lite:
	@COMPOSE_FILE=$(LITE_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) ps

ps-full:
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) ps

logs-lite:
	@COMPOSE_FILE=$(LITE_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) logs -f --tail=200

logs-full:
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) logs -f --tail=200

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
ifeq ($(PODMAN_SELECTED),true)
	@set -eu; \
	for image in $(ONYX_REQUIRED_IMAGES); do \
		echo "Pulling required Onyx image directly with Podman: $$image"; \
		"$(CONTAINER_BIN)" pull "$$image"; \
	done
else
	@if [ -f "$(ONYX_ENV_FILE)" ]; then \
		if grep -q '^IMAGE_TAG=' "$(ONYX_ENV_FILE)"; then \
			sed -i.bak "s|^IMAGE_TAG=.*|IMAGE_TAG=$(ONYX_IMAGE_TAG)|" "$(ONYX_ENV_FILE)"; \
		else \
			printf '\nIMAGE_TAG=%s\n' "$(ONYX_IMAGE_TAG)" >> "$(ONYX_ENV_FILE)"; \
		fi; \
		echo "Updated $(ONYX_ENV_FILE): IMAGE_TAG=$(ONYX_IMAGE_TAG)"; \
	fi
	@echo "Running Onyx install script to prepare images (args: $(ONYX_INSTALL_ARGS))..."
	@cd onyx && CONTAINER_BIN="$(CONTAINER_BIN)" PODMAN_COMPOSE_PROVIDER="$(PODMAN_COMPOSE_PROVIDER)" ONYX_DESIRED_IMAGE_TAG="$(ONYX_IMAGE_TAG)" ONYX_INSTALL_SCRIPT="$(ONYX_INSTALL_SCRIPT)" HOST_PORT_80="$(ONYX_INSTALL_HOST_PORT_80)" bash "$(ONYX_INSTALL_WRAPPER)" --no-prompt $(ONYX_INSTALL_ARGS)
	@set -eu; \
	for image in $(ONYX_REQUIRED_IMAGES); do \
		echo "Ensuring Onyx image tag is present: $$image"; \
		"$(CONTAINER_BIN)" pull "$$image"; \
	done
	@cd onyx && CONTAINER_BIN="$(CONTAINER_BIN)" PODMAN_COMPOSE_PROVIDER="$(PODMAN_COMPOSE_PROVIDER)" ONYX_DESIRED_IMAGE_TAG="$(ONYX_IMAGE_TAG)" ONYX_INSTALL_SCRIPT="$(ONYX_INSTALL_SCRIPT)" HOST_PORT_80="$(ONYX_INSTALL_HOST_PORT_80)" bash "$(ONYX_INSTALL_WRAPPER)" --shutdown $(ONYX_INSTALL_ARGS) >/dev/null 2>&1 || true
endif

myst-image-ready:
	@if "$(CONTAINER_BIN)" image inspect "$(MYST_IMAGE)" >/dev/null 2>&1; then \
		echo "Myst image already present: $(MYST_IMAGE)"; \
	else \
		echo "Myst image not found: $(MYST_IMAGE). Building..."; \
		$(MAKE) myst-build MYST_IMAGE="$(MYST_IMAGE)" MYST_NODE_REPO="$(MYST_NODE_REPO)" MYST_NODE_REF="$(MYST_NODE_REF)" MYST_DOCKERFILE="$(MYST_DOCKERFILE)"; \
	fi

myst-build:
	@echo "Building $(MYST_IMAGE) using $(MYST_DOCKERFILE) (repo=$(MYST_NODE_REPO), ref=$(MYST_NODE_REF))..."
	@set -eu; set --; \
	[ -z "$${HTTP_PROXY:-}" ] || set -- "$$@" --build-arg HTTP_PROXY; \
	[ -z "$${HTTPS_PROXY:-}" ] || set -- "$$@" --build-arg HTTPS_PROXY; \
	[ -z "$${NO_PROXY:-}" ] || set -- "$$@" --build-arg NO_PROXY; \
	[ -z "$${http_proxy:-}" ] || set -- "$$@" --build-arg http_proxy; \
	[ -z "$${https_proxy:-}" ] || set -- "$$@" --build-arg https_proxy; \
	[ -z "$${no_proxy:-}" ] || set -- "$$@" --build-arg no_proxy; \
	"$(CONTAINER_BIN)" build "$$@" \
		--file "$(MYST_DOCKERFILE)" \
		--build-arg MYST_NODE_REPO="$(MYST_NODE_REPO)" \
		--build-arg MYST_NODE_REF="$(MYST_NODE_REF)" \
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
	@set -eu; set --; \
	[ -z "$${HTTP_PROXY:-}" ] || set -- "$$@" --build-arg HTTP_PROXY; \
	[ -z "$${HTTPS_PROXY:-}" ] || set -- "$$@" --build-arg HTTPS_PROXY; \
	[ -z "$${NO_PROXY:-}" ] || set -- "$$@" --build-arg NO_PROXY; \
	[ -z "$${http_proxy:-}" ] || set -- "$$@" --build-arg http_proxy; \
	[ -z "$${https_proxy:-}" ] || set -- "$$@" --build-arg https_proxy; \
	[ -z "$${no_proxy:-}" ] || set -- "$$@" --build-arg no_proxy; \
	"$(CONTAINER_BIN)" build "$$@" \
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
	model_repo="$${ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL:-majentik/harrier-oss-v1-0.6b-MLX-8bit}"; \
	venv_python="$(PWD)/$(EMBEDSERV_VENV)/bin/python"; \
	model_dir="$(PWD)/$(EMBEDSERV_MODEL_CACHE)/$$model_repo"; \
	mkdir -p "$(EMBEDSERV_DIR)" "$$(dirname "$$model_dir")"; \
	if [ ! -x "$$venv_python" ]; then \
		uv venv --python 3.13 "$(EMBEDSERV_VENV)"; \
	fi; \
	uv pip install --python "$$venv_python" --require-hashes -r "$(EMBEDSERV_REQUIREMENTS)"; \
	echo "Downloading MLX embedding model: $$model_repo"; \
	MODEL_REPO="$$model_repo" MODEL_DIR="$$model_dir" "$$venv_python" -c 'import os; from huggingface_hub import snapshot_download; snapshot_download(repo_id=os.environ["MODEL_REPO"], local_dir=os.environ["MODEL_DIR"])'; \
	echo "Model ready at $$model_dir"

ifeq ($(filter true,$(EMBEDSERV_SKIP_INSTALL)),)
embedserv-verify-model: embedserv-install
endif
embedserv-verify-model:
	@set -eu; \
	if [ ! -f "$(ENV_FILE)" ]; then \
		echo "ERROR: missing $(ENV_FILE)"; \
		exit 1; \
	fi; \
	set -a; . "$(ENV_FILE)"; set +a; \
	model_repo="$${ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL:-majentik/harrier-oss-v1-0.6b-MLX-8bit}"; \
	model_dir="$(PWD)/$(EMBEDSERV_MODEL_CACHE)/$$model_repo"; \
	if [ ! -d "$$model_dir" ]; then \
		echo "ERROR: model directory missing: $$model_dir"; \
		exit 1; \
	fi; \
	echo "Verifying served model directory: $$model_dir"; \
	verify_out_file=$$(mktemp); \
	if "$(PWD)/$(EMBEDSERV_VENV)/bin/hf" cache verify "$$model_repo" \
		--local-dir "$$model_dir" \
		--fail-on-missing-files >"$$verify_out_file" 2>&1; then \
		cat "$$verify_out_file"; \
		rm -f -- "$$verify_out_file"; \
	else \
		verify_rc=$$?; \
		cat "$$verify_out_file"; \
		rm -f -- "$$verify_out_file"; \
		echo "ERROR: local embedserv model verification failed"; \
		exit "$$verify_rc"; \
	fi

embedserv-serve: embedserv-verify-model
	@set -eu; \
	if [ ! -f "$(ENV_FILE)" ]; then \
		echo "ERROR: missing $(ENV_FILE)"; \
		exit 1; \
	fi; \
	set -a; . "$(ENV_FILE)"; set +a; \
	model_repo="$${ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL:-majentik/harrier-oss-v1-0.6b-MLX-8bit}"; \
	served_model="$${ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL:-$$model_repo}"; \
	embeddings_url="$${ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL:-http://host.docker.internal:3210/v1/embeddings}"; \
	model_dir="$(PWD)/$(EMBEDSERV_MODEL_CACHE)/$$model_repo"; \
	if [ "$$embeddings_url" != "$(EMBEDSERV_DEFAULT_UPSTREAM_URL)" ]; then \
		echo "ERROR: embedserv-serve owns only the bundled default upstream: $(EMBEDSERV_DEFAULT_UPSTREAM_URL)"; \
		exit 1; \
	fi; \
	echo "Launching idle lifecycle proxy for $$served_model on 127.0.0.1:3210"; \
	exec python3 "$(EMBEDSERV_DIR)/idle_embedding_proxy.py" \
		--listen-port 3210 \
		--child-port 3211 \
		--server-executable "$(PWD)/$(EMBEDSERV_VENV)/bin/mlx-openai-server" \
		--model-path "$$model_dir" \
		--served-model-name "$$served_model" \
		--child-pid-file "$(PWD)/$(EMBEDSERV_CHILD_PID_FILE)"

# Start the bundled host MLX server for full mode only after its selected model
# has been installed and only while the shim still targets the bundled default
# endpoint. Custom endpoints (including teep) remain entirely operator-owned.
embedserv-start-if-installed:
	@set -eu; \
	if [ ! -f "$(ENV_FILE)" ]; then \
		echo "ERROR: missing $(ENV_FILE)"; \
		exit 1; \
	fi; \
	set -a; . "$(ENV_FILE)"; set +a; \
	model_repo="$${ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL:-majentik/harrier-oss-v1-0.6b-MLX-8bit}"; \
	served_model="$${ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL:-$$model_repo}"; \
	embeddings_url="$${ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL:-$(EMBEDSERV_DEFAULT_UPSTREAM_URL)}"; \
	venv_server="$(PWD)/$(EMBEDSERV_VENV)/bin/mlx-openai-server"; \
	model_dir="$(PWD)/$(EMBEDSERV_MODEL_CACHE)/$$model_repo"; \
	proxy_script="$(PWD)/$(EMBEDSERV_DIR)/idle_embedding_proxy.py"; \
	child_pid_file="$(PWD)/$(EMBEDSERV_CHILD_PID_FILE)"; \
	config_id=$$(python3 -c 'import hashlib,sys; print(hashlib.sha256("\0".join(sys.argv[1:]).encode()).hexdigest())' \
		"$$proxy_script" "3210" "3211" "$$venv_server" "$$model_dir" "$$served_model" "$$child_pid_file"); \
	if [ "$$embeddings_url" != "$(EMBEDSERV_DEFAULT_UPSTREAM_URL)" ]; then \
		echo "Embedding shim uses a custom upstream; not starting bundled MLX server: $$embeddings_url"; \
		exit 0; \
	fi; \
	if python3 -c 'import socket; s=socket.create_connection(("127.0.0.1", 3210), timeout=1); s.close()' >/dev/null 2>&1; then \
		if [ -f "$(EMBEDSERV_PID_FILE)" ]; then \
			recorded_pid=$$(sed -n '1p' "$(EMBEDSERV_PID_FILE)"); \
			owner_token=$$(sed -n '2p' "$(EMBEDSERV_PID_FILE)"); \
			recorded_config_id=$$(sed -n '3p' "$(EMBEDSERV_PID_FILE)"); \
			recorded_command=""; \
			case "$$recorded_pid" in \
				''|*[!0-9]*) ;; \
				*) if current_command=$$(ps -p "$$recorded_pid" -o command= 2>/dev/null); then recorded_command="$$current_command"; fi ;; \
			esac; \
			owned=false; \
			if python3 -c 'import re,sys; raise SystemExit(0 if re.fullmatch(r"[0-9a-f]{64}", sys.argv[1]) else 1)' "$$owner_token"; then \
				case "$$recorded_command" in *"$$proxy_script "*"--owner-token $$owner_token"*) owned=true ;; esac; \
			fi; \
			if [ "$$owned" = true ] && [ "$$recorded_config_id" = "$$config_id" ]; then \
					echo "The wrapper-managed MLX lifecycle proxy is already listening on 127.0.0.1:3210"; \
					exit 0; \
			fi; \
			if [ "$$owned" = true ]; then \
				echo "Restarting the wrapper-managed MLX lifecycle proxy after configuration changed"; \
				kill -TERM "$$recorded_pid"; \
				attempt=0; \
				while kill -0 "$$recorded_pid" 2>/dev/null && [ "$$attempt" -lt 500 ]; do sleep 0.1; attempt=$$((attempt + 1)); done; \
				if kill -0 "$$recorded_pid" 2>/dev/null; then echo "ERROR: prior MLX lifecycle proxy did not stop"; exit 1; fi; \
				rm -f -- "$(EMBEDSERV_PID_FILE)"; \
			else \
				echo "ERROR: port 3210 is occupied but the recorded automatic proxy identity does not match"; \
				echo "Inspect $(EMBEDSERV_PID_FILE) and $(EMBEDSERV_LOG); no process was signaled"; \
				exit 1; \
			fi; \
		else \
			echo "An operator-managed embedding server is already listening on 127.0.0.1:3210; not starting another one"; \
			exit 0; \
		fi; \
	fi; \
	if [ ! -x "$$venv_server" ] || [ ! -d "$$model_dir" ]; then \
		echo "ERROR: the default embedding upstream is selected, but the bundled MLX server is not installed for $$model_repo"; \
		echo "Run 'make embedserv-install' or configure ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL for Teep/custom embeddings"; \
		exit 1; \
	fi; \
	echo "Starting bundled MLX embedding lifecycle proxy for $$model_repo (log: $(EMBEDSERV_LOG))"; \
	owner_token=$$(python3 -c 'import secrets; print(secrets.token_hex(32))'); \
	pid=$$(python3 -c 'import subprocess,sys; log=open(sys.argv[1], "wb", buffering=0); child=subprocess.Popen(sys.argv[2:], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True); print(child.pid)' \
		"$(EMBEDSERV_LOG)" python3 "$$proxy_script" \
		--listen-port 3210 \
		--child-port 3211 \
		--server-executable "$$venv_server" \
		--model-path "$$model_dir" \
		--served-model-name "$$served_model" \
		--child-pid-file "$$child_pid_file" \
		--owner-token "$$owner_token"); \
	printf '%s\n%s\n%s\n' "$$pid" "$$owner_token" "$$config_id" >"$(EMBEDSERV_PID_FILE)"; \
	attempt=0; \
	while [ "$$attempt" -lt 240 ]; do \
		if python3 -c 'import socket; s=socket.create_connection(("127.0.0.1", 3210), timeout=1); s.close()' >/dev/null 2>&1; then \
			echo "Bundled MLX embedding lifecycle proxy is listening on port 3210"; \
			exit 0; \
		fi; \
		if ! kill -0 "$$pid" 2>/dev/null; then \
			echo "ERROR: bundled MLX embedding server exited during startup; recent log output follows:"; \
			tail -n 40 "$(EMBEDSERV_LOG)"; \
			rm -f -- "$(EMBEDSERV_PID_FILE)"; \
			exit 1; \
		fi; \
		sleep 0.5; \
		attempt=$$((attempt + 1)); \
	done; \
	echo "ERROR: bundled MLX embedding server did not listen on 127.0.0.1:3210 within 120 seconds"; \
	tail -n 40 "$(EMBEDSERV_LOG)"; \
	kill -TERM "$$pid"; \
	exit 1

# Stop only the lifecycle proxy recorded by the automatic full-mode startup. An
# absent, exited, malformed, or reused PID is a diagnosed no-op so down-full is
# not made brittle by stale host state. Foreground/manual servers are untouched.
embedserv-stop-if-started:
	@set -eu; \
	if [ ! -f "$(EMBEDSERV_PID_FILE)" ]; then \
		echo "No automatically started MLX embedding server PID is recorded"; \
		exit 0; \
	fi; \
	pid=$$(sed -n '1p' "$(EMBEDSERV_PID_FILE)"); \
	owner_token=$$(sed -n '2p' "$(EMBEDSERV_PID_FILE)"); \
	case "$$pid" in \
		''|*[!0-9]*) \
			echo "Ignoring malformed MLX embedding server PID file: $(EMBEDSERV_PID_FILE)"; \
			rm -f -- "$(EMBEDSERV_PID_FILE)"; \
			exit 0; \
			;; \
	esac; \
	if ! python3 -c 'import re,sys; raise SystemExit(0 if re.fullmatch(r"[0-9a-f]{64}", sys.argv[1]) else 1)' "$$owner_token"; then \
		echo "Ignoring MLX embedding server record with an invalid ownership token"; \
		rm -f -- "$(EMBEDSERV_PID_FILE)"; \
		exit 0; \
	fi; \
	command_line=""; \
	if current_command=$$(ps -p "$$pid" -o command= 2>/dev/null); then \
		command_line="$$current_command"; \
	fi; \
	if [ -z "$$command_line" ]; then \
		echo "Automatically started MLX embedding server is no longer running (stale PID $$pid)"; \
		rm -f -- "$(EMBEDSERV_PID_FILE)"; \
		exit 0; \
	fi; \
	case "$$command_line" in \
		*'$(PWD)/embedserv/idle_embedding_proxy.py '*"--owner-token $$owner_token"*) ;; \
		*) \
			echo "PID $$pid no longer belongs to the automatically started MLX embedding server; leaving it untouched"; \
			rm -f -- "$(EMBEDSERV_PID_FILE)"; \
			exit 0; \
			;; \
	esac; \
	echo "Stopping automatically started MLX embedding server (PID $$pid)"; \
	if ! kill -TERM "$$pid"; then \
		echo "ERROR: could not signal automatically started MLX embedding server PID $$pid"; \
		exit 1; \
	fi; \
	attempt=0; \
	while kill -0 "$$pid" 2>/dev/null && [ "$$attempt" -lt 500 ]; do \
		sleep 0.1; \
		attempt=$$((attempt + 1)); \
	done; \
	if kill -0 "$$pid" 2>/dev/null; then \
		echo "ERROR: MLX lifecycle proxy PID $$pid did not stop after its bounded request and child grace; inspect $(EMBEDSERV_LOG)"; \
		exit 1; \
	fi; \
	rm -f -- "$(EMBEDSERV_PID_FILE)"; \
	echo "Automatically started MLX embedding server stopped"

embedserv-cleanup-recorded-child:
	@set -eu; \
	if [ ! -f "$(EMBEDSERV_CHILD_PID_FILE)" ]; then exit 0; fi; \
	set -a; . "$(ENV_FILE)"; set +a; \
	model_repo="$${ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL:-majentik/harrier-oss-v1-0.6b-MLX-8bit}"; \
	served_model="$${ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL:-$$model_repo}"; \
	python3 "$(PWD)/$(EMBEDSERV_DIR)/idle_embedding_proxy.py" \
		--child-port 3211 \
		--server-executable "$(PWD)/$(EMBEDSERV_VENV)/bin/mlx-openai-server" \
		--model-path "$(PWD)/$(EMBEDSERV_MODEL_CACHE)/$$model_repo" \
		--served-model-name "$$served_model" \
		--child-pid-file "$(PWD)/$(EMBEDSERV_CHILD_PID_FILE)" \
		--cleanup-recorded-child

# ══════════════════════════════════════════════════════════════════════════════
# VPN signup, order status, and balance
# ══════════════════════════════════════════════════════════════════════════════

# Start standalone Myst container for initial signup/payment, then run the
# signup helper which creates an identity, registers it, and creates a
# payment order. The payment URL is printed to stdout.
# MYST_AUTO_CONNECT and MYST_VPN_WAIT_FOR_FUNDS are disabled during signup.
vpn-signup-orderform: myst-image-ready
	@set -eu; \
	if "$(CONTAINER_BIN)" inspect -f '{{.State.Running}}' $(MYST_CONTAINER_NAME) 2>/dev/null | grep -q true; then \
		echo "Myst container '$(MYST_CONTAINER_NAME)' is already running."; \
	else \
		echo "Starting standalone Myst signup container..."; \
		mkdir -p $(MYST_DATA_DIR); \
		MYST_AUTO_CONNECT=false MYST_VPN_WAIT_FOR_FUNDS=false \
			COMPOSE_FILE=$(MYST_COMPOSE_FILE) "$(CONTAINER_BIN)" compose $(COMPOSE_ENV_FILES) up -d; \
		echo "Waiting for container to initialize..."; \
		sleep 3; \
	fi
	@CONTAINER_BIN="$(CONTAINER_BIN)" CONTAINER_NAME="$(MYST_CONTAINER_NAME)" \
		$(MYST_VPN_CLI) signup

# Start standalone Myst container for initial signup, then run the blockchain
# helper which creates an identity, registers it, and prints the consumer
# channel address for direct on-chain $MYST transfer (Polygon). No payment
# order is created.
# MYST_AUTO_CONNECT and MYST_VPN_WAIT_FOR_FUNDS are disabled during signup.
# MYST_SKIP_ORDER_CREATION prevents the entrypoint from auto-creating a
# CoinGate order.
vpn-signup-blockchain: myst-image-ready
	@set -eu; \
	if "$(CONTAINER_BIN)" inspect -f '{{.State.Running}}' $(MYST_CONTAINER_NAME) 2>/dev/null | grep -q true; then \
		echo "Myst container '$(MYST_CONTAINER_NAME)' is already running."; \
	else \
		echo "Starting standalone Myst signup container..."; \
		mkdir -p $(MYST_DATA_DIR); \
		MYST_AUTO_CONNECT=false MYST_VPN_WAIT_FOR_FUNDS=false MYST_SKIP_ORDER_CREATION=true \
			COMPOSE_FILE=$(MYST_COMPOSE_FILE) "$(CONTAINER_BIN)" compose $(COMPOSE_ENV_FILES) up -d; \
		echo "Waiting for container to initialize..."; \
		sleep 3; \
	fi
	@CONTAINER_BIN="$(CONTAINER_BIN)" CONTAINER_NAME="$(MYST_CONTAINER_NAME)" \
		$(MYST_VPN_CLI) blockchain

# Show identity, balance, registration status, all orders, and payment URLs
# for any unpaid orders. Works against whichever myst container is running.
vpn-orderstatus:
	@CONTAINER_BIN="$(CONTAINER_BIN)" CONTAINER_NAME="$(MYST_CONTAINER_NAME)" \
		$(MYST_VPN_CLI) orderstatus

# Quick balance check. Works against whichever myst container is running.
vpn-balance:
	@CONTAINER_BIN="$(CONTAINER_BIN)" CONTAINER_NAME="$(MYST_CONTAINER_NAME)" \
		$(MYST_VPN_CLI) balance

# Prerequisite for up-lite/up-full: stop signup container if running and
# verify that a Myst identity (keystore) exists. If no keystore is found,
# instruct the user to run 'make vpn-signup-orderform' or 'make vpn-signup-blockchain' first.
ensure-myst-funded:
	@set -eu; \
	if [ "$(MYST_VPN_ENABLED)" = "false" ]; then \
		echo "MYST_VPN_ENABLED=false — skipping Myst keystore/funding check."; \
		exit 0; \
	fi; \
	if "$(CONTAINER_BIN)" inspect -f '{{.State.Running}}' $(MYST_CONTAINER_NAME) 2>/dev/null | grep -q true; then \
		myst_project=""; \
		if myst_project="$$($(CONTAINER_BIN) inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' $(MYST_CONTAINER_NAME) 2>/dev/null)"; then :; fi; \
		if [ "$$myst_project" = "onyx" ]; then \
			echo "Integrated Onyx Myst container is already running; preserving its routing namespace."; \
		else \
			echo "Stopping standalone Myst signup container (wallet data is preserved)..."; \
			COMPOSE_FILE=$(MYST_COMPOSE_FILE) "$(CONTAINER_BIN)" compose $(COMPOSE_ENV_FILES) down --remove-orphans 2>/dev/null || \
				"$(CONTAINER_BIN)" stop $(MYST_CONTAINER_NAME) 2>/dev/null || true; \
			"$(CONTAINER_BIN)" rm -f $(MYST_CONTAINER_NAME) 2>/dev/null || true; \
		fi; \
	fi; \
	if [ ! -d "$(MYST_DATA_DIR)/keystore" ] || [ -z "$$(ls -A $(MYST_DATA_DIR)/keystore 2>/dev/null)" ]; then \
		echo ""; \
		echo "ERROR: No Myst identity found in $(MYST_DATA_DIR)/keystore/"; \
		echo "       You need to sign up and fund your VPN wallet first."; \
		echo ""; \
		echo "       Run: make vpn-signup-orderform  (pay via CoinGate order page)"; \
		echo "    OR: make vpn-signup-blockchain    (transfer MYST directly on Polygon)"; \
		echo "       Then check with: make vpn-orderstatus"; \
		echo "       Once funded, run: make up-lite (or make up-full)"; \
		echo ""; \
		exit 1; \
	else \
		echo "Myst identity found in $(MYST_DATA_DIR)/keystore/ — proceeding."; \
	fi
