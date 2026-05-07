ENV_FILE ?= .env.wrapper
WRAPPER_FILE := docker-compose.yaml
ONYX_FULL_FILE := onyx/onyx_data/deployment/docker-compose.yml
ONYX_LITE_FILE := onyx/onyx_data/deployment/docker-compose.onyx-lite.yml

LITE_FILES := $(WRAPPER_FILE):$(ONYX_FULL_FILE):$(ONYX_LITE_FILE)
FULL_FILES := $(WRAPPER_FILE):$(ONYX_FULL_FILE)

.PHONY: help up up-lite up-full down down-lite down-full ps-lite ps-full logs-lite logs-full config-lite config-full pull-lite pull-full

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
	@echo ""
	@echo "Override env file: make up ENV_FILE=.env.wrapper"

up: up-lite

down: down-lite

up-lite:
	@COMPOSE_FILE=$(LITE_FILES) docker compose --env-file $(ENV_FILE) up -d --wait

up-full:
	@COMPOSE_FILE=$(FULL_FILES) docker compose --env-file $(ENV_FILE) up -d --wait

down-lite:
	@COMPOSE_FILE=$(LITE_FILES) docker compose --env-file $(ENV_FILE) down

down-full:
	@COMPOSE_FILE=$(FULL_FILES) docker compose --env-file $(ENV_FILE) down

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
