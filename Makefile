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

# Source of truth: ONYX_IMAGE_TAG in $(ENV_FILE). Allow CLI override.
ONYX_IMAGE_TAG ?= $(strip $(shell sed -n 's/^ONYX_IMAGE_TAG=//p' "$(ENV_FILE)" 2>/dev/null | head -1))
ifeq ($(strip $(ONYX_IMAGE_TAG)),)
$(error ONYX_IMAGE_TAG is not set. Add ONYX_IMAGE_TAG=... to $(ENV_FILE), or pass ONYX_IMAGE_TAG=... on the make command line)
endif
ONYX_BACKEND_IMAGE ?= onyxdotapp/onyx-backend:$(ONYX_IMAGE_TAG)
ONYX_WEB_SERVER_IMAGE ?= onyxdotapp/onyx-web-server:$(ONYX_IMAGE_TAG)
ONYX_MODEL_SERVER_IMAGE ?= onyxdotapp/onyx-model-server:$(ONYX_IMAGE_TAG)
ONYX_INSTALL_SCRIPT ?= ./install.sh
ONYX_ENV_FILE ?= onyx/onyx_data/deployment/.env
ONYX_CONFIG_REF ?= $(ONYX_IMAGE_TAG)
SEARXNG_COMPOSE_FILE := searxng/docker-compose.yml
SEARXNG_ENV_FILE := searxng/.env

LITE_FILES := $(WRAPPER_FILE):$(LITE_OVERRIDE_FILE)
FULL_FILES := $(WRAPPER_FILE):$(FULL_OVERRIDE_FILE)

.PHONY: help up-lite up-full down-lite down-full ps-lite ps-full logs-lite logs-full ensure-onyx-config sync-onyx-env upgrade upgrade-onyx searxng-image-ready myst-image-ready myst-build teep-image-ready teep-build onyx-image-ready onyx-build

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
	@echo "  make upgrade      # Rebuild Myst + teep images, refresh Onyx deployment files, and pull SearxNG"
	@echo "  make upgrade-onyx # Download fresh Onyx deployment files for ONYX_IMAGE_TAG"
	@echo "  make onyx-build   # Pull/build Onyx images via onyx/install.sh"
	@echo "  make myst-build   # Build Myst image from myst/build/Dockerfile"
	@echo "  make teep-build   # Build teep image from teep/build/Dockerfile"
	@echo ""
	@echo "Override env file: make up-lite ENV_FILE=.env.wrapper"
	@echo "Override Onyx tag: make onyx-build ONYX_IMAGE_TAG=v3.2.12"
	@echo "Override config ref: make upgrade-onyx ONYX_CONFIG_REF=main"
	@echo "Override Myst image: make myst-build MYST_IMAGE=local/myst:docker_host_fixes_with_logs"
	@echo "Override teep image: make teep-build TEEP_IMAGE=local/teep:main"

upgrade: myst-build teep-build searxng-image-ready upgrade-onyx

up-lite: ONYX_INSTALL_ARGS=--lite
up-lite: ONYX_REQUIRED_IMAGES=$(ONYX_BACKEND_IMAGE) $(ONYX_WEB_SERVER_IMAGE)
up-lite: ensure-onyx-config sync-onyx-env onyx-image-ready myst-image-ready teep-image-ready
	@COMPOSE_FILE=$(LITE_FILES) docker compose --env-file $(ENV_FILE) up -d --wait

up-full: ONYX_INSTALL_ARGS=
up-full: ONYX_REQUIRED_IMAGES=$(ONYX_BACKEND_IMAGE) $(ONYX_WEB_SERVER_IMAGE) $(ONYX_MODEL_SERVER_IMAGE)
up-full: ensure-onyx-config sync-onyx-env onyx-image-ready myst-image-ready teep-image-ready
	@COMPOSE_FILE=$(FULL_FILES) docker compose --env-file $(ENV_FILE) up -d --wait

ensure-onyx-config:
	@set -eu; \
	if [ ! -f "$(ONYX_ENV_FILE)" ]; then \
		echo "$(ONYX_ENV_FILE) missing; running upgrade-onyx for $(ONYX_IMAGE_TAG)"; \
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
	@$(MAKE) sync-onyx-env

searxng-image-ready:
	@set -eu; \
	image=$$(docker compose --env-file "$(SEARXNG_ENV_FILE)" -f "$(SEARXNG_COMPOSE_FILE)" config | sed -n 's/^    image: //p' | head -1); \
	if [ -z "$$image" ]; then \
		echo "ERROR: could not resolve SearxNG image from $(SEARXNG_COMPOSE_FILE)"; \
		exit 1; \
	fi; \
	echo "Pulling SearxNG image: $$image"; \
	docker pull "$$image"

down-lite:
	@COMPOSE_FILE=$(LITE_FILES) docker compose --env-file $(ENV_FILE) down --remove-orphans

down-full:
	@COMPOSE_FILE=$(FULL_FILES) docker compose --env-file $(ENV_FILE) down --remove-orphans

ps-lite:
	@COMPOSE_FILE=$(LITE_FILES) docker compose --env-file $(ENV_FILE) ps

ps-full:
	@COMPOSE_FILE=$(FULL_FILES) docker compose --env-file $(ENV_FILE) ps

logs-lite:
	@COMPOSE_FILE=$(LITE_FILES) docker compose --env-file $(ENV_FILE) logs -f --tail=200

logs-full:
	@COMPOSE_FILE=$(FULL_FILES) docker compose --env-file $(ENV_FILE) logs -f --tail=200

onyx-image-ready:
	@set -eu; \
	missing=0; \
	for image in $(ONYX_REQUIRED_IMAGES); do \
		if docker image inspect "$$image" >/dev/null 2>&1; then \
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
		docker image inspect "$$image" >/dev/null 2>&1 || { echo "ERROR: Onyx image still missing after onyx-build: $$image"; exit 1; }; \
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
	@cd onyx && bash "$(ONYX_INSTALL_SCRIPT)" --no-prompt $(ONYX_INSTALL_ARGS)
	@set -eu; \
	for image in $(ONYX_REQUIRED_IMAGES); do \
		echo "Ensuring Onyx image tag is present: $$image"; \
		docker pull "$$image"; \
	done
	@cd onyx && bash "$(ONYX_INSTALL_SCRIPT)" --shutdown $(ONYX_INSTALL_ARGS) >/dev/null 2>&1 || true

myst-image-ready:
	@if docker image inspect "$(MYST_IMAGE)" >/dev/null 2>&1; then \
		echo "Myst image already present: $(MYST_IMAGE)"; \
	else \
		echo "Myst image not found: $(MYST_IMAGE). Building..."; \
		$(MAKE) myst-build MYST_IMAGE="$(MYST_IMAGE)" MYST_NODE_REPO="$(MYST_NODE_REPO)" MYST_NODE_BRANCH="$(MYST_NODE_BRANCH)" MYST_DOCKERFILE="$(MYST_DOCKERFILE)"; \
	fi

myst-build:
	@echo "Building $(MYST_IMAGE) using $(MYST_DOCKERFILE) (repo=$(MYST_NODE_REPO), branch=$(MYST_NODE_BRANCH))..."
	@docker build \
		--file "$(MYST_DOCKERFILE)" \
		--build-arg MYST_NODE_REPO="$(MYST_NODE_REPO)" \
		--build-arg MYST_NODE_BRANCH="$(MYST_NODE_BRANCH)" \
		--tag "$(MYST_IMAGE)" \
		.

teep-image-ready:
	@if docker image inspect "$(TEEP_IMAGE)" >/dev/null 2>&1; then \
		echo "teep image already present: $(TEEP_IMAGE)"; \
	else \
		echo "teep image not found: $(TEEP_IMAGE). Building..."; \
		$(MAKE) teep-build TEEP_IMAGE="$(TEEP_IMAGE)" TEEP_REPO="$(TEEP_REPO)" TEEP_REF="$(TEEP_REF)" TEEP_DOCKERFILE="$(TEEP_DOCKERFILE)"; \
	fi

teep-build:
	@echo "Building $(TEEP_IMAGE) using $(TEEP_DOCKERFILE) (repo=$(TEEP_REPO), ref=$(TEEP_REF))..."
	@docker build \
		--file "$(TEEP_DOCKERFILE)" \
		--build-arg TEEP_REPO="$(TEEP_REPO)" \
		--build-arg TEEP_REF="$(TEEP_REF)" \
		--tag "$(TEEP_IMAGE)" \
		.
