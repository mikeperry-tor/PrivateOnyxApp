ENV_FILE ?= .env.wrapper
WRAPPER_FILE := docker-compose.yaml
ONYX_LITE_FILE := onyx/onyx_data/deployment/docker-compose.onyx-lite.yml

MYST_NODE_REPO ?= https://github.com/mikeperry-tor/node.git
MYST_NODE_BRANCH ?= docker_host_fixes_with_logs
MYST_DOCKERFILE ?= myst/build/Dockerfile
MYST_IMAGE ?= mysteriumnetwork/myst:docker_host_fixes_with_logs

LITE_FILES := $(WRAPPER_FILE):$(ONYX_LITE_FILE)
FULL_FILES := $(WRAPPER_FILE)

.PHONY: help up up-lite up-full down down-lite down-full ps-lite ps-full logs-lite logs-full config-lite config-full pull-lite pull-full myst-build

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
	@echo "  make myst-build   # Build Myst image from myst/build/Dockerfile"
	@echo ""
	@echo "Override env file: make up ENV_FILE=.env.wrapper"
	@echo "Override Myst image: make myst-build MYST_IMAGE=local/myst:docker_host_fixes_with_logs"

up: up-lite

down: down-lite

up-lite:
	@COMPOSE_FILE=$(LITE_FILES) docker compose --env-file $(ENV_FILE) up -d --wait

up-full:
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

myst-build:
	@echo "Building $(MYST_IMAGE) using $(MYST_DOCKERFILE) (repo=$(MYST_NODE_REPO), branch=$(MYST_NODE_BRANCH))..."
	@docker build \
		--file "$(MYST_DOCKERFILE)" \
		--build-arg MYST_NODE_REPO="$(MYST_NODE_REPO)" \
		--build-arg MYST_NODE_BRANCH="$(MYST_NODE_BRANCH)" \
		--tag "$(MYST_IMAGE)" \
		.
