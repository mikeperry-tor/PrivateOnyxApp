ENV_FILE ?= .env.wrapper
VERSION_FILE ?= stack.versions.env
WRAPPER_FILE := docker-compose.yaml
FULL_OVERRIDE_FILE := docker-compose.full.yml
LITE_OVERRIDE_FILE := docker-compose.lite.yml
PODMAN_OVERRIDE_FILE := docker-compose.podman.yml
PODMAN_FULL_OVERRIDE_FILE := docker-compose.podman-full.yml

env_value = $(strip $(shell for f in "$(ENV_FILE)" "$(VERSION_FILE)"; do [ -f "$$f" ] || continue; sed -n 's/^$(1)=//p' "$$f" | head -1 | sed 's/^"//; s/"$$//'; done | head -1))
COMPOSE_ENV_FILES = --env-file "$(VERSION_FILE)" --env-file "$(ENV_FILE)"
ONYX_COMPOSE_ENV_FILES = $(COMPOSE_ENV_FILES) --env-file "$(ONYX_ENV_FILE)"

MYST_NODE_REPO ?= https://github.com/mikeperry-tor/node.git
MYST_NODE_BRANCH ?= docker_host_fixes_with_logs
MYST_DOCKERFILE ?= myst/build/Dockerfile
MYST_IMAGE ?= $(call env_value,MYST_IMAGE)
ifeq ($(strip $(MYST_IMAGE)),)
MYST_IMAGE := mysteriumnetwork/myst:docker_host_fixes_with_logs
endif

TEEP_REPO ?= https://github.com/13rac1/teep.git
TEEP_DEFAULT_REF := 46ee4b854641d3932c880ae5ac66d5f2d2a26791
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
TAILSCALE_IMAGE := tailscale/tailscale:stable
endif
OBSCURA_IMAGE ?= $(call env_value,OBSCURA_IMAGE)
ifeq ($(strip $(OBSCURA_IMAGE)),)
OBSCURA_IMAGE := h4ckf0r0day/obscura:0.1.9
endif
CRW_IMAGE ?= $(call env_value,CRW_IMAGE)
ifeq ($(strip $(CRW_IMAGE)),)
CRW_IMAGE := ghcr.io/us/crw:0.18.3
endif
CONTAINER_BIN ?= $(call env_value,CONTAINER_BIN)
DOCKER_SOCK_PATH ?= $(call env_value,DOCKER_SOCK_PATH)
TEEP_ROUTE_THROUGH_MYST_VPN ?= $(call env_value,TEEP_ROUTE_THROUGH_MYST_VPN)
TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN ?= $(call env_value,TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN)
ONYX_CODE_INTERPRETER_ENABLE_NETWORK ?= $(call env_value,ONYX_CODE_INTERPRETER_ENABLE_NETWORK)
MYST_VPN_ENABLED ?= $(call env_value,MYST_VPN_ENABLED)
ONYX_AGENT_OUTBOUND_PROXY_URL ?= $(call env_value,ONYX_AGENT_OUTBOUND_PROXY_URL)
# obscura and crw's SOCKS proxy connectors cannot resolve Docker-internal DNS
# names (host.docker.internal) -- they try to resolve the proxy hostname
# through the SOCKS proxy itself, which fails. Derive the resolved proxy URL by
# replacing host.docker.internal with its resolved IP address. If
# ONYX_AGENT_OUTBOUND_PROXY_URL is empty or already uses an IP/public hostname,
# the resolved value equals ONYX_AGENT_OUTBOUND_PROXY_URL.
ONYX_AGENT_OUTBOUND_PROXY_URL_RESOLVED :=
ifneq ($(strip $(ONYX_AGENT_OUTBOUND_PROXY_URL)),)
PROXY_HOST_INTERNAL := $(strip $(shell echo "$(ONYX_AGENT_OUTBOUND_PROXY_URL)" | grep -oE 'host\.docker\.internal' | head -1))
ifneq ($(strip $(PROXY_HOST_INTERNAL)),)
# Resolve host.docker.internal via Docker (run a one-shot container). Falls
# back to getent on the host if Docker resolution fails.
PROXY_HOST_IP := $(strip $(shell "$(CONTAINER_BIN)" run --rm alpine:3.20 sh -c 'getent hosts host.docker.internal 2>/dev/null | awk "{print \$$1}" | head -1' 2>/dev/null || getent hosts host.docker.internal 2>/dev/null | awk '{print $$1}' | head -1 || true))
ifneq ($(strip $(PROXY_HOST_IP)),)
ONYX_AGENT_OUTBOUND_PROXY_URL_RESOLVED := $(strip $(shell echo "$(ONYX_AGENT_OUTBOUND_PROXY_URL)" | sed 's/host\.docker\.internal/$(PROXY_HOST_IP)/g'))
else
# Could not resolve; fall back to the original URL.
ONYX_AGENT_OUTBOUND_PROXY_URL_RESOLVED := $(ONYX_AGENT_OUTBOUND_PROXY_URL)
endif
else
ONYX_AGENT_OUTBOUND_PROXY_URL_RESOLVED := $(ONYX_AGENT_OUTBOUND_PROXY_URL)
endif
endif
export ONYX_AGENT_OUTBOUND_PROXY_URL_RESOLVED
PODMAN_COMPOSE_PROVIDER ?= podman
ifeq ($(strip $(CONTAINER_BIN)),)
CONTAINER_BIN := docker
endif
ifeq ($(strip $(DOCKER_SOCK_PATH)),)
ifneq ($(findstring podman,$(CONTAINER_BIN)),)
DOCKER_SOCK_PATH := $(strip $(shell "$(CONTAINER_BIN)" machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}' 2>/dev/null | head -1))
endif
endif
export CONTAINER_BIN
export DOCKER_SOCK_PATH
export TEEP_IMAGE

PODMAN_COMPOSE_SUFFIX :=
PODMAN_FULL_COMPOSE_SUFFIX :=
ifneq ($(findstring podman,$(CONTAINER_BIN)),)
PODMAN_COMPOSE_SUFFIX :=:$(PODMAN_OVERRIDE_FILE)
PODMAN_FULL_COMPOSE_SUFFIX :=:$(PODMAN_FULL_OVERRIDE_FILE)
endif

# Conditional routing overrides for teep and tailscale.
# When TEEP_ROUTE_THROUGH_MYST_VPN=true, teep joins the netns-holder namespace.
TEEP_VPN_SUFFIX :=
ifneq ($(filter true,$(TEEP_ROUTE_THROUGH_MYST_VPN)),)
TEEP_VPN_SUFFIX :=:docker-compose.teep-vpn.yml
endif

# When TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN=true, tailscale joins the
# netns-holder namespace.
TAILSCALE_VPN_SUFFIX :=
ifneq ($(filter true,$(TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN)),)
TAILSCALE_VPN_SUFFIX :=:docker-compose.tailscale-vpn.yml
endif

# When ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true, code-interpreter executor
# pods inherit the code-interpreter's shared netns via a sitecustomize patch.
CODE_INTERPRETER_VPN_SUFFIX :=
ifneq ($(filter true,$(ONYX_CODE_INTERPRETER_ENABLE_NETWORK)),)
CODE_INTERPRETER_VPN_SUFFIX :=:docker-compose.code-interpreter-vpn.yml
endif

# When ONYX_AGENT_OUTBOUND_PROXY_URL is non-empty, apply the proxy override
# layer that threads a single upstream proxy through crw, obscura (CDP + MCP),
# SearXNG, the code-interpreter, and the code agent. See docker-compose.proxy.yml.
PROXY_SUFFIX :=
ifneq ($(strip $(ONYX_AGENT_OUTBOUND_PROXY_URL)),)
PROXY_SUFFIX :=:docker-compose.proxy.yml
endif

# Source of truth: ONYX_IMAGE_TAG in $(VERSION_FILE). Allow ENV_FILE or CLI override.
ONYX_IMAGE_TAG ?= $(call env_value,ONYX_IMAGE_TAG)
ifeq ($(strip $(ONYX_IMAGE_TAG)),)
$(error ONYX_IMAGE_TAG is not set. Add ONYX_IMAGE_TAG=... to $(VERSION_FILE), override it in $(ENV_FILE), or pass ONYX_IMAGE_TAG=... on the make command line)
endif
ONYX_BACKEND_IMAGE ?= onyxdotapp/onyx-backend:$(ONYX_IMAGE_TAG)
ONYX_WEB_SERVER_IMAGE ?= onyxdotapp/onyx-web-server:$(ONYX_IMAGE_TAG)
ONYX_MODEL_SERVER_IMAGE ?= onyxdotapp/onyx-model-server:$(ONYX_IMAGE_TAG)
SEARXNG_IMAGE_TAG ?= $(call env_value,SEARXNG_IMAGE_TAG)
ifeq ($(strip $(SEARXNG_IMAGE_TAG)),)
$(error SEARXNG_IMAGE_TAG is not set. Add SEARXNG_IMAGE_TAG=... to $(VERSION_FILE), override it in $(ENV_FILE), or pass SEARXNG_IMAGE_TAG=... on the make command line)
endif
SEARXNG_IMAGE ?= docker.io/searxng/searxng:$(SEARXNG_IMAGE_TAG)
export SEARXNG_IMAGE_TAG
SEARXNG_SECRET := $(strip $(shell openssl rand -hex 32 2>/dev/null))
USER_AUTH_SECRET := $(strip $(shell openssl rand -hex 32 2>/dev/null))
CRW_ONYX_API_KEY := crw-$(strip $(shell openssl rand -hex 16 2>/dev/null))
MINIO_ROOT_USER := $(strip $(shell openssl rand -hex 16 2>/dev/null))
MINIO_ROOT_PASSWORD := $(strip $(shell openssl rand -hex 32 2>/dev/null))
S3_AWS_ACCESS_KEY_ID := $(MINIO_ROOT_USER)
S3_AWS_SECRET_ACCESS_KEY := $(MINIO_ROOT_PASSWORD)
ifeq ($(strip $(SEARXNG_SECRET)$(USER_AUTH_SECRET)$(MINIO_ROOT_PASSWORD)),)
$(error openssl is required to generate ephemeral local stack secrets)
endif
export SEARXNG_SECRET
export USER_AUTH_SECRET
export CRW_ONYX_API_KEY
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
CDP_SHIM_REQUIREMENTS_IN := crw/cdp-shim-requirements.in
CDP_SHIM_REQUIREMENTS := crw/cdp-shim-requirements.txt
UV_CACHE_DIR ?= /tmp/private-onyx-uv-cache

LITE_FILES := $(WRAPPER_FILE):$(LITE_OVERRIDE_FILE)$(PODMAN_COMPOSE_SUFFIX)$(TEEP_VPN_SUFFIX)$(TAILSCALE_VPN_SUFFIX)$(CODE_INTERPRETER_VPN_SUFFIX)$(PROXY_SUFFIX)
FULL_FILES := $(WRAPPER_FILE):$(FULL_OVERRIDE_FILE)$(PODMAN_COMPOSE_SUFFIX)$(PODMAN_FULL_COMPOSE_SUFFIX)$(TEEP_VPN_SUFFIX)$(TAILSCALE_VPN_SUFFIX)$(CODE_INTERPRETER_VPN_SUFFIX)$(PROXY_SUFFIX)

.PHONY: help up-lite up-full down-lite down-full ps-lite ps-full logs-lite logs-full ensure-onyx-config init-onyx-env sync-onyx-env upgrade upgrade-onyx upgrade-python-deps searxng-image-ready tailscale-image-ready crw-image-ready myst-image-ready myst-build teep-image-ready teep-build onyx-image-ready onyx-build embedserv-install embedserv-verify-model embedserv-serve vpn-signup-orderform vpn-signup-blockchain vpn-orderstatus vpn-balance ensure-myst-funded

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
	@echo "  make upgrade      # Upgrade Python locks, rebuild Myst + teep, refresh Onyx deployment files, and pull companion images"
	@echo "  make upgrade-onyx # Download fresh Onyx deployment files for ONYX_IMAGE_TAG"
	@echo "  make upgrade-python-deps # Upgrade hashed Python lock files from requirements.in inputs"
	@echo "  make onyx-build   # Pull/build Onyx images via onyx/install.sh"
	@echo "  make myst-build   # Build Myst image from myst/build/Dockerfile"
	@echo "  make teep-build   # Build teep image from teep/build/Dockerfile"
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
	@echo "Override Onyx tag: make onyx-build ONYX_IMAGE_TAG=v3.2.12"
	@echo "Override SearXNG tag: make searxng-image-ready SEARXNG_IMAGE_TAG=2026.6.26-f8ffbf36f"
	@echo "Override code-interpreter tag: make up-lite CODE_INTERPRETER_IMAGE_TAG=0.4.4"
	@echo "Override config ref: make upgrade-onyx ONYX_CONFIG_REF=main"
	@echo "Override install-time low-port remap: make up-full ONYX_INSTALL_HOST_PORT_80=3001"
	@echo "Override container engine: make up-lite CONTAINER_BIN=/opt/homebrew/bin/podman"
	@echo "Note: podman mode applies $(PODMAN_OVERRIDE_FILE) (disables code-interpreter + autoheal by default)"
	@echo "VPN routing: set TEEP_ROUTE_THROUGH_MYST_VPN=true or"
	@echo "             TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN=true in $(ENV_FILE)"
	@echo "Code interpreter network: set ONYX_CODE_INTERPRETER_ENABLE_NETWORK=true in $(ENV_FILE)"
	@echo "Disable VPN: set MYST_VPN_ENABLED=false in $(ENV_FILE) to idle myst-client without kill-switch/connect"
	@echo "Proxy: set ONYX_AGENT_OUTBOUND_PROXY_URL in $(ENV_FILE) (http/https/socks5)"
	@echo "       to route crw, obscura, SearXNG, code-interpreter, and code agent egress"
	@echo "Override Myst image: make myst-build MYST_IMAGE=local/myst:docker_host_fixes_with_logs"
	@echo "Override teep pin: make teep-build TEEP_REF=<commit-sha>"
	@echo "Override teep image: make teep-build TEEP_IMAGE=local/teep:<tag>"
	@echo "Override embedding model: make embedserv-install ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL=majentik/harrier-oss-v1-0.6b-MLX-8bit"

upgrade: upgrade-python-deps myst-build teep-build searxng-image-ready tailscale-image-ready crw-image-ready upgrade-onyx

upgrade-python-deps:
	@set -eu; \
	if ! command -v uv >/dev/null 2>&1; then \
		echo "ERROR: uv is required for upgrade-python-deps"; \
		exit 1; \
	fi; \
	UV_CACHE_DIR="$(UV_CACHE_DIR)" uv pip compile --upgrade --generate-hashes "$(EMBEDSERV_REQUIREMENTS_IN)" -o "$(EMBEDSERV_REQUIREMENTS)"; \
	UV_CACHE_DIR="$(UV_CACHE_DIR)" uv pip compile --upgrade --generate-hashes "$(CDP_SHIM_REQUIREMENTS_IN)" -o "$(CDP_SHIM_REQUIREMENTS)"

tailscale-image-ready:
	@echo "Pulling Tailscale image: $(TAILSCALE_IMAGE)"; \
	"$(CONTAINER_BIN)" pull "$(TAILSCALE_IMAGE)"

crw-image-ready:
	@echo "Pulling obscura image: $(OBSCURA_IMAGE)"; \
	"$(CONTAINER_BIN)" pull "$(OBSCURA_IMAGE)"
	@echo "Pulling crw image: $(CRW_IMAGE)"; \
	"$(CONTAINER_BIN)" pull "$(CRW_IMAGE)"

up-lite: ONYX_INSTALL_ARGS=--lite
up-lite: ONYX_REQUIRED_IMAGES=$(ONYX_BACKEND_IMAGE) $(ONYX_WEB_SERVER_IMAGE) $(CODE_INTERPRETER_IMAGE)
up-lite: ensure-onyx-config sync-onyx-env ensure-myst-funded onyx-image-ready myst-image-ready teep-image-ready
	@COMPOSE_FILE=$(LITE_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) up -d --wait

up-full: ONYX_INSTALL_ARGS=
up-full: ONYX_REQUIRED_IMAGES=$(ONYX_BACKEND_IMAGE) $(ONYX_WEB_SERVER_IMAGE) $(ONYX_MODEL_SERVER_IMAGE) $(CODE_INTERPRETER_IMAGE)
up-full: ensure-onyx-config sync-onyx-env ensure-myst-funded onyx-image-ready myst-image-ready teep-image-ready
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) up -d --wait

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

searxng-image-ready:
	@set -eu; \
	image=$$("$(CONTAINER_BIN)" compose $(COMPOSE_ENV_FILES) -f "$(SEARXNG_COMPOSE_FILE)" config | sed -n 's/^    image: //p' | head -1); \
	if [ -z "$$image" ]; then \
		echo "ERROR: could not resolve SearxNG image from $(SEARXNG_COMPOSE_FILE)"; \
		exit 1; \
	fi; \
	echo "Pulling SearxNG image: $$image"; \
	"$(CONTAINER_BIN)" pull "$$image"; \
	echo "SearxNG image ready: $$image"

down-lite:
	@COMPOSE_FILE=$(LITE_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) down --remove-orphans

down-full:
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) down --remove-orphans

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
	model_repo="$${ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL:-majentik/harrier-oss-v1-0.6b-MLX-8bit}"; \
	venv_python="$(PWD)/$(EMBEDSERV_VENV)/bin/python"; \
	model_dir="$(PWD)/$(EMBEDSERV_MODEL_CACHE)/$$model_repo"; \
	mkdir -p "$(EMBEDSERV_DIR)" "$$(dirname "$$model_dir")"; \
	if [ ! -x "$$venv_python" ]; then \
		uv venv --python 3.12 "$(EMBEDSERV_VENV)"; \
	fi; \
	uv pip install --python "$$venv_python" --require-hashes -r "$(EMBEDSERV_REQUIREMENTS)"; \
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
	model_repo="$${ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL:-majentik/harrier-oss-v1-0.6b-MLX-8bit}"; \
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
	model_repo="$${ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL:-majentik/harrier-oss-v1-0.6b-MLX-8bit}"; \
	served_model="$${ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL:-$$model_repo}"; \
	embeddings_url="$${ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL:-http://host.docker.internal:3210/v1/embeddings}"; \
	model_dir="$(PWD)/$(EMBEDSERV_MODEL_CACHE)/$$model_repo"; \
	parsed_url=$$(URL="$$embeddings_url" python3 -c 'from urllib.parse import urlparse; import os; parsed = urlparse(os.environ["URL"]); print(parsed.scheme); print(parsed.hostname or ""); print("" if parsed.port is None else parsed.port); print(parsed.path.rstrip("/"))'); \
	scheme=$$(printf '%s\n' "$$parsed_url" | sed -n '1p'); \
	host=$$(printf '%s\n' "$$parsed_url" | sed -n '2p'); \
	port=$$(printf '%s\n' "$$parsed_url" | sed -n '3p'); \
	path=$$(printf '%s\n' "$$parsed_url" | sed -n '4p'); \
	if [ "$$scheme" != "http" ]; then \
		echo "ERROR: ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL must use http: $$embeddings_url"; \
		exit 1; \
	fi; \
	if [ -z "$$host" ] || [ -z "$$port" ]; then \
		echo "ERROR: ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL must include a host and explicit port: $$embeddings_url"; \
		exit 1; \
	fi; \
	if [ "$$path" != "/v1/embeddings" ]; then \
		echo "ERROR: ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL must end with /v1/embeddings: $$embeddings_url"; \
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
		echo "Stopping standalone Myst signup container (wallet data is preserved)..."; \
		COMPOSE_FILE=$(MYST_COMPOSE_FILE) "$(CONTAINER_BIN)" compose $(COMPOSE_ENV_FILES) down --remove-orphans 2>/dev/null || \
			"$(CONTAINER_BIN)" stop $(MYST_CONTAINER_NAME) 2>/dev/null || true; \
		"$(CONTAINER_BIN)" rm -f $(MYST_CONTAINER_NAME) 2>/dev/null || true; \
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
