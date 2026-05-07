ENV_FILE ?= .env.wrapper
WRAPPER_FILE := docker-compose.yaml
ONYX_LITE_FILE := onyx/onyx_data/deployment/docker-compose.onyx-lite.yml

MYST_NODE_REPO ?= https://github.com/mikeperry-tor/node.git
MYST_NODE_BRANCH ?= docker_host_fixes_with_logs
MYST_DOCKERFILE ?= myst/build/Dockerfile
MYST_IMAGE ?= mysteriumnetwork/myst:docker_host_fixes_with_logs

TEEP_REPO ?= https://github.com/13rac1/teep.git
TEEP_REF ?= main
TEEP_DOCKERFILE ?= teep/build/Dockerfile
TEEP_IMAGE ?= 13rac1/teep:main

LITE_FILES := $(WRAPPER_FILE):$(ONYX_LITE_FILE)
FULL_FILES := $(WRAPPER_FILE)

.PHONY: help up up-lite up-full down down-lite down-full ps-lite ps-full logs-lite logs-full config-lite config-full pull-lite pull-full myst-image-ready myst-build teep-image-ready teep-build

help:
	@echo "Targets:"
	@echo "  make up           # Start in lite mode (default)"
	@echo "  make up-lite      # Start wrapper + Onyx lite"
	@echo "  make up-full      # Start wrapper + Onyx full"
	@echo "  make down         # Stop lite mode stack (default)"
	@echo "  make down-lite    # Stop wrapper + Onyx lite"
	@echo "  make down-full    # Stop wrapper + Onyx full"
	@echo "  make ps-lite      # Show lite mode containers"
	@echo "  make ps-full      # Show full mode containers"
	@echo "  make logs-lite    # Tail lite mode logs"
	@echo "  make logs-full    # Tail full mode logs"
	@echo "  make config-lite  # Render lite compose config"
	@echo "  make config-full  # Render full compose config"
	@echo "  make pull-lite    # Pull images for lite mode"
	@echo "  make pull-full    # Pull images for full mode"
	@echo "  make myst-image-ready # Build Myst image only if missing"
	@echo "  make myst-build   # Build Myst image from myst/build/Dockerfile"
	@echo "  make teep-image-ready # Build teep image only if missing"
	@echo "  make teep-build   # Build teep image from teep/build/Dockerfile"
	@echo ""
	@echo "Override env file: make up ENV_FILE=.env.wrapper"
	@echo "Override Myst image: make myst-build MYST_IMAGE=local/myst:docker_host_fixes_with_logs"
	@echo "Override teep image: make teep-build TEEP_IMAGE=local/teep:main"

up: up-lite

down: down-lite

up-lite: myst-image-ready teep-image-ready
	@COMPOSE_FILE=$(LITE_FILES) docker compose --env-file $(ENV_FILE) up -d --wait

up-full: myst-image-ready teep-image-ready
	@COMPOSE_FILE=$(FULL_FILES) docker compose --env-file $(ENV_FILE) up -d --wait

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

config-lite:
	@COMPOSE_FILE=$(LITE_FILES) docker compose --env-file $(ENV_FILE) config

config-full:
	@COMPOSE_FILE=$(FULL_FILES) docker compose --env-file $(ENV_FILE) config

pull-lite:
	@COMPOSE_FILE=$(LITE_FILES) docker compose --env-file $(ENV_FILE) pull

pull-full:
	@COMPOSE_FILE=$(FULL_FILES) docker compose --env-file $(ENV_FILE) pull

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
