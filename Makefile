# Deterministic checks must not parse or inherit selections from the operator's
# private runtime configuration. Apply this before any settings are read.
ifneq ($(strip $(filter test check check-upgrade,$(MAKECMDGOALS))),)
override ENV_FILE := .env.wrapper.example
else
ENV_FILE ?= .env.wrapper
endif
VERSION_FILE ?= stack.versions.env
WRAPPER_FILE := docker-compose.yaml
COMPOSE_OVERLAY_DIR := compose_overlays
FULL_OVERRIDE_FILE := $(COMPOSE_OVERLAY_DIR)/docker-compose.full.yml
LITE_OVERRIDE_FILE := $(COMPOSE_OVERLAY_DIR)/docker-compose.lite.yml
PODMAN_OVERRIDE_FILE := $(COMPOSE_OVERLAY_DIR)/docker-compose.podman.yml
PODMAN_FULL_OVERRIDE_FILE := $(COMPOSE_OVERLAY_DIR)/docker-compose.podman-full.yml
PODMAN_MACOS_FULL_OVERRIDE_FILE := $(COMPOSE_OVERLAY_DIR)/docker-compose.podman-macos-full.yml
DOCKER_LINUX_OVERRIDE_FILE := $(COMPOSE_OVERLAY_DIR)/docker-compose.docker-linux.yml
TOR_COMMON_FILE := $(COMPOSE_OVERLAY_DIR)/docker-compose.tor.yml
TOR_EGRESS_FILE := $(COMPOSE_OVERLAY_DIR)/docker-compose.tor-egress.yml
TOR_ONION_FILE := $(COMPOSE_OVERLAY_DIR)/docker-compose.tor-onion.yml
TOR_PODMAN_FILE := $(COMPOSE_OVERLAY_DIR)/docker-compose.tor-podman.yml
TOR_ONION_PODMAN_FILE := $(COMPOSE_OVERLAY_DIR)/docker-compose.tor-onion-podman.yml

env_value = $(strip $(shell for f in "$(ENV_FILE)" "$(VERSION_FILE)"; do [ -f "$$f" ] || continue; sed -n 's/^$(1)=//p' "$$f" | head -1 | sed 's/^"//; s/"$$//'; done | head -1))
wrapper_setting = $(strip $(shell python3 tor/render_config.py get --settings-file "$(ENV_FILE)" --name "$(1)"))
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
TEEP_IMAGE := docker.io/13rac1/teep:$(TEEP_REF)
endif
TEEP_IMAGE := $(TEEP_IMAGE)
TAILSCALE_IMAGE ?= $(call env_value,TAILSCALE_IMAGE)
ifeq ($(strip $(TAILSCALE_IMAGE)),)
$(error TAILSCALE_IMAGE is not set in $(VERSION_FILE))
endif
PYTHON_SLIM_IMAGE ?= $(call env_value,PYTHON_SLIM_IMAGE)
PYTHON_ALPINE_IMAGE ?= $(call env_value,PYTHON_ALPINE_IMAGE)
OBSCURA_RELEASE_VERSION ?= $(call env_value,OBSCURA_RELEASE_VERSION)
OBSCURA_RELEASE_AMD64_SHA256 ?= $(call env_value,OBSCURA_RELEASE_AMD64_SHA256)
OBSCURA_RELEASE_ARM64_SHA256 ?= $(call env_value,OBSCURA_RELEASE_ARM64_SHA256)
OBSCURA_UPSTREAM_IMAGE ?= $(call env_value,OBSCURA_UPSTREAM_IMAGE)
OBSCURA_WRAPPER_IMAGE_REPOSITORY ?= $(call env_value,OBSCURA_WRAPPER_IMAGE_REPOSITORY)
ifeq ($(strip $(OBSCURA_RELEASE_VERSION)),)
$(error OBSCURA_RELEASE_VERSION is not set in $(VERSION_FILE))
endif
ifeq ($(strip $(OBSCURA_RELEASE_AMD64_SHA256)),)
$(error OBSCURA_RELEASE_AMD64_SHA256 is not set in $(VERSION_FILE))
endif
ifeq ($(strip $(OBSCURA_RELEASE_ARM64_SHA256)),)
$(error OBSCURA_RELEASE_ARM64_SHA256 is not set in $(VERSION_FILE))
endif
ifeq ($(strip $(OBSCURA_UPSTREAM_IMAGE)),)
$(error OBSCURA_UPSTREAM_IMAGE is not set in $(VERSION_FILE))
endif
ifeq ($(strip $(OBSCURA_WRAPPER_IMAGE_REPOSITORY)),)
$(error OBSCURA_WRAPPER_IMAGE_REPOSITORY is not set in $(VERSION_FILE))
endif
OBSCURA_DOCKERFILE ?= browser/obscura_image/Dockerfile
CONTAINER_BIN ?= $(call env_value,CONTAINER_BIN)
DOCKER_SOCK_PATH ?= $(call env_value,DOCKER_SOCK_PATH)
TEEP_ROUTE_THROUGH_MYST_VPN ?= $(call env_value,TEEP_ROUTE_THROUGH_MYST_VPN)
TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN ?= $(call env_value,TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN)
TAILSCALE_FUNNEL_ENABLED ?= $(call env_value,TAILSCALE_FUNNEL_ENABLED)
ONYX_CODE_INTERPRETER_ENABLE_NETWORK ?= $(call env_value,ONYX_CODE_INTERPRETER_ENABLE_NETWORK)
MYST_VPN_ENABLED ?= $(call env_value,MYST_VPN_ENABLED)
ifeq ($(strip $(TEEP_ROUTE_THROUGH_MYST_VPN)),)
TEEP_ROUTE_THROUGH_MYST_VPN := false
endif
ifeq ($(strip $(TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN)),)
TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN := false
endif
ifeq ($(strip $(TAILSCALE_FUNNEL_ENABLED)),)
TAILSCALE_FUNNEL_ENABLED := false
endif
ifeq ($(strip $(ONYX_CODE_INTERPRETER_ENABLE_NETWORK)),)
ONYX_CODE_INTERPRETER_ENABLE_NETWORK := false
endif
ifeq ($(strip $(MYST_VPN_ENABLED)),)
MYST_VPN_ENABLED := false
endif
CONTAINER_BIN := $(CONTAINER_BIN)
DOCKER_SOCK_PATH := $(DOCKER_SOCK_PATH)
EGRESS_UPSTREAM_PROXY_URL ?= $(call wrapper_setting,EGRESS_UPSTREAM_PROXY_URL)
TOR_EGRESS_ENABLED ?= $(call wrapper_setting,TOR_EGRESS_ENABLED)
TOR_ONION_SERVICE_ENABLED ?= $(call wrapper_setting,TOR_ONION_SERVICE_ENABLED)
# Freeze command-line/environment values before Make can reinterpret an
# optional leading '$' in a Tor relay fingerprint. File values are read only
# by the shared wrapper-settings parser.
ifneq ($(filter command line environment,$(origin TOR_EXIT_NODE_FINGERPRINTS)),)
override TOR_EXIT_NODE_FINGERPRINTS := $(value TOR_EXIT_NODE_FINGERPRINTS)
export TOR_EXIT_NODE_FINGERPRINTS
endif
# Freeze this before exporting it. GNU Make 4.4 can otherwise recursively
# expand the $(shell) lookup while constructing that lookup's own environment,
# self-shadow the file value with an empty export, or overflow recursively.
ifneq ($(filter command line environment,$(origin WEBUI_CANONICAL_ORIGIN)),)
override WEBUI_CANONICAL_ORIGIN := $(value WEBUI_CANONICAL_ORIGIN)
else
WEBUI_CANONICAL_ORIGIN := $(call wrapper_setting,WEBUI_CANONICAL_ORIGIN)
endif
# Freeze environment/command-line values before Make can reinterpret literal
# dollars. File-origin values come only from the restricted settings reader.
ifneq ($(filter command line environment,$(origin ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL)),)
override ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL := $(value ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL)
else
ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL := $(call wrapper_setting,ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL)
endif
ifneq ($(filter command line environment,$(origin ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL)),)
override ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL := $(value ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL)
else
ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL := $(call wrapper_setting,ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL)
endif
ifneq ($(filter command line environment,$(origin ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL)),)
override ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL := $(value ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL)
else
ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL := $(call wrapper_setting,ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL)
endif
EMBEDSERV_DEFAULT_UPSTREAM_URL := http://host.docker.internal:3210/v1/embeddings
EMBEDSERV_DEFAULT_MODEL := majentik/harrier-oss-v1-0.6b-MLX-8bit
ifeq ($(strip $(ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL)),)
override ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL := $(EMBEDSERV_DEFAULT_UPSTREAM_URL)
endif
ifeq ($(strip $(ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL)),)
override ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL := $(EMBEDSERV_DEFAULT_MODEL)
endif
ifeq ($(strip $(ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL)),)
override ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL := $(ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL)
endif
EMBEDSERV_MODE := $(if $(filter $(EMBEDSERV_DEFAULT_UPSTREAM_URL),$(ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL)),bundled,custom)
export ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL
export ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL
export ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL
PODMAN_COMPOSE_PROVIDER ?= podman
ifeq ($(strip $(CONTAINER_BIN)),)
CONTAINER_BIN := docker
endif
PODMAN_SELECTED := $(if $(findstring podman,$(notdir $(CONTAINER_BIN))),true,false)
SHARED_DATA_ENGINE := $(if $(filter true,$(PODMAN_SELECTED)),podman,docker)
SHARED_DATA_ENGINE_MARKER := docker-data/host-services/shared-data-engine
SHARED_DATA_GUARD_ENV := $(if $(filter true,$(PODMAN_SELECTED)),env -u DOCKER_HOST,)
ifeq ($(strip $(DOCKER_SOCK_PATH)),)
ifneq ($(findstring podman,$(CONTAINER_BIN)),)
DOCKER_SOCK_PATH := $(strip $(shell "$(CONTAINER_BIN)" machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}' 2>/dev/null | sed -n '\|^/|{p;q;}'))
ifeq ($(strip $(DOCKER_SOCK_PATH)),)
DOCKER_SOCK_PATH := $(strip $(shell "$(CONTAINER_BIN)" info --format '{{.Host.RemoteSocket.Path}}' 2>/dev/null | sed -n '\|^/|{p;q;}'))
endif
endif
endif
export CONTAINER_BIN
export DOCKER_SOCK_PATH
ifeq ($(PODMAN_SELECTED),true)
# Docker Compose 5.3.x may otherwise honor the host's selected Docker context
# or Podman's SSH system connection. Pin the external provider to the current
# selected engine's local compatibility socket; native engine commands still
# use CONTAINER_BIN=podman.
ifneq ($(strip $(DOCKER_SOCK_PATH)),)
export DOCKER_HOST := unix://$(DOCKER_SOCK_PATH)
endif
endif
export TEEP_IMAGE
export WEBUI_CANONICAL_ORIGIN

ifneq ($(filter true,$(TAILSCALE_FUNNEL_ENABLED)),)
export COMPOSE_PROFILES := tailscale
endif

PODMAN_COMPOSE_SUFFIX :=
PODMAN_FULL_COMPOSE_SUFFIX :=
PODMAN_MACOS_FULL_COMPOSE_SUFFIX :=
DOCKER_LINUX_COMPOSE_SUFFIX :=
HOST_OS ?= $(shell uname -s)
ifneq ($(findstring podman,$(CONTAINER_BIN)),)
PODMAN_COMPOSE_SUFFIX :=:$(PODMAN_OVERRIDE_FILE)
PODMAN_FULL_COMPOSE_SUFFIX :=:$(PODMAN_FULL_OVERRIDE_FILE)
ifeq ($(HOST_OS),Darwin)
PODMAN_MACOS_FULL_COMPOSE_SUFFIX :=:$(PODMAN_MACOS_FULL_OVERRIDE_FILE)
endif
else ifeq ($(HOST_OS),Linux)
DOCKER_LINUX_COMPOSE_SUFFIX :=:$(DOCKER_LINUX_OVERRIDE_FILE)
endif
PRIVATE_ONYX_HOST_UID := $(shell id -u)
PRIVATE_ONYX_HOST_GID := $(shell id -g)
export PRIVATE_ONYX_HOST_UID
export PRIVATE_ONYX_HOST_GID

# Conditional routing overrides for teep and tailscale.
# When true, a fixed Teep gateway joins the trusted routing namespace; the
# Onyx caller and Teep service remain on explicit internal networks.
TEEP_VPN_SUFFIX :=
TEEP_EMBEDDING_HOST_PUBLISHER :=
ifneq ($(filter true,$(TEEP_ROUTE_THROUGH_MYST_VPN)),)
TEEP_VPN_SUFFIX :=:$(COMPOSE_OVERLAY_DIR)/docker-compose.teep-vpn.yml
TEEP_EMBEDDING_HOST_PUBLISHER := host-teep-proxy
endif

# When true, Tailscale shares the trusted route namespace but still reaches
# Onyx only through its fixed frontend gateway.
TAILSCALE_VPN_SUFFIX :=
ifneq ($(filter true,$(TAILSCALE_FUNNEL_ROUTE_THROUGH_MYST_VPN)),)
TAILSCALE_VPN_SUFFIX :=:$(COMPOSE_OVERLAY_DIR)/docker-compose.tailscale-vpn.yml
endif

# When enabled, executor pods join only the named internal executor network.
CODE_INTERPRETER_NETWORK_SUFFIX :=
ifneq ($(filter true,$(ONYX_CODE_INTERPRETER_ENABLE_NETWORK)),)
ifneq ($(PODMAN_SELECTED),true)
CODE_INTERPRETER_NETWORK_SUFFIX :=:$(COMPOSE_OVERLAY_DIR)/docker-compose.code-interpreter-network.yml
endif
endif

TOR_BASE_IMAGE ?= $(call env_value,TOR_BASE_IMAGE)
ifeq ($(strip $(TOR_BASE_IMAGE)),)
$(error TOR_BASE_IMAGE is not set in $(VERSION_FILE))
endif
TOR_WRAPPER_IMAGE_REPOSITORY ?= $(call env_value,TOR_WRAPPER_IMAGE_REPOSITORY)
ifeq ($(strip $(TOR_WRAPPER_IMAGE_REPOSITORY)),)
$(error TOR_WRAPPER_IMAGE_REPOSITORY is not set in $(VERSION_FILE))
endif
TOR_DOCKERFILE := tor/Dockerfile
TOR_WRAPPER_SOURCE_HASH := $(shell python3 -c 'import hashlib,pathlib,sys; h=hashlib.sha256(sys.argv[1].encode()+b"\0"); h.update(pathlib.Path(sys.argv[2]).read_bytes()); print(h.hexdigest()[:12])' '$(TOR_BASE_IMAGE)' '$(TOR_DOCKERFILE)')
TOR_IMAGE ?= $(TOR_WRAPPER_IMAGE_REPOSITORY):0.4.9.11-$(TOR_WRAPPER_SOURCE_HASH)
TOR_IMAGE := $(TOR_IMAGE)
export TOR_IMAGE

TOR_COMMON_SUFFIX :=
TOR_EGRESS_SUFFIX :=
TOR_ONION_SUFFIX :=
TOR_PODMAN_SUFFIX :=
TOR_ONION_PODMAN_SUFFIX :=
ifneq ($(filter true,$(TOR_EGRESS_ENABLED) $(TOR_ONION_SERVICE_ENABLED)),)
TOR_COMMON_SUFFIX :=:$(TOR_COMMON_FILE)
ifneq ($(PODMAN_SELECTED),false)
TOR_PODMAN_SUFFIX :=:$(TOR_PODMAN_FILE)
endif
endif
ifneq ($(filter true,$(TOR_EGRESS_ENABLED)),)
TOR_EGRESS_SUFFIX :=:$(TOR_EGRESS_FILE)
endif
ifneq ($(filter true,$(TOR_ONION_SERVICE_ENABLED)),)
TOR_ONION_SUFFIX :=:$(TOR_ONION_FILE)
ifneq ($(PODMAN_SELECTED),false)
TOR_ONION_PODMAN_SUFFIX :=:$(TOR_ONION_PODMAN_FILE)
endif
endif
TOR_SUFFIX := $(TOR_COMMON_SUFFIX)$(TOR_EGRESS_SUFFIX)$(TOR_ONION_SUFFIX)$(TOR_PODMAN_SUFFIX)$(TOR_ONION_PODMAN_SUFFIX)
TOR_DOWN_PODMAN_SUFFIX :=
ifneq ($(PODMAN_SELECTED),false)
TOR_DOWN_PODMAN_SUFFIX :=:$(TOR_PODMAN_FILE):$(TOR_ONION_PODMAN_FILE)
endif
TOR_DOWN_SUFFIX :=:$(TOR_COMMON_FILE):$(TOR_EGRESS_FILE):$(TOR_ONION_FILE)$(TOR_DOWN_PODMAN_SUFFIX)

# Source of truth: ONYX_IMAGE_TAG in $(VERSION_FILE). Allow ENV_FILE or CLI override.
ONYX_IMAGE_TAG ?= $(call env_value,ONYX_IMAGE_TAG)
ifeq ($(strip $(ONYX_IMAGE_TAG)),)
$(error ONYX_IMAGE_TAG is not set. Add ONYX_IMAGE_TAG=... to $(VERSION_FILE), override it in $(ENV_FILE), or pass ONYX_IMAGE_TAG=... on the make command line)
endif
ONYX_BACKEND_IMAGE ?= docker.io/onyxdotapp/onyx-backend:$(ONYX_IMAGE_TAG)
ONYX_WEB_SERVER_IMAGE ?= docker.io/onyxdotapp/onyx-web-server:$(ONYX_IMAGE_TAG)
SEARXNG_IMAGE_TAG ?= $(call env_value,SEARXNG_IMAGE_TAG)
ifeq ($(strip $(SEARXNG_IMAGE_TAG)),)
$(error SEARXNG_IMAGE_TAG is not set. Add SEARXNG_IMAGE_TAG=... to $(VERSION_FILE), override it in $(ENV_FILE), or pass SEARXNG_IMAGE_TAG=... on the make command line)
endif
SEARXNG_IMAGE_TAG := $(SEARXNG_IMAGE_TAG)
SEARXNG_IMAGE ?= docker.io/searxng/searxng:$(SEARXNG_IMAGE_TAG)
SEARXNG_DOCKERFILE ?= searxng/Dockerfile
SEARXNG_WRAPPER_IMAGE_REPOSITORY ?= $(call env_value,SEARXNG_WRAPPER_IMAGE_REPOSITORY)
ifeq ($(strip $(SEARXNG_WRAPPER_IMAGE_REPOSITORY)),)
$(error SEARXNG_WRAPPER_IMAGE_REPOSITORY is not set. Add it to $(VERSION_FILE) or override it on the make command line)
endif
SEARXNG_WRAPPER_IMAGE_REPOSITORY := $(SEARXNG_WRAPPER_IMAGE_REPOSITORY)
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
SEARXNG_WRAPPER_IMAGE := $(SEARXNG_WRAPPER_IMAGE)
OBSCURA_WRAPPER_BUILD_INPUTS := \
	$(OBSCURA_DOCKERFILE) \
	browser/obscura_image/fetch_release.py
OBSCURA_WRAPPER_SOURCE_HASH := $(shell python3 -c 'import hashlib,pathlib,sys; h=hashlib.sha256(); [h.update(v.encode()+b"\0") for v in sys.argv[1:4]]; [h.update(p.encode()+b"\0"+pathlib.Path(p).read_bytes()) for p in sys.argv[4:]]; print(h.hexdigest()[:12])' "$(OBSCURA_UPSTREAM_IMAGE)" "$(OBSCURA_RELEASE_AMD64_SHA256)" "$(OBSCURA_RELEASE_ARM64_SHA256)" $(OBSCURA_WRAPPER_BUILD_INPUTS))
ifeq ($(strip $(OBSCURA_WRAPPER_SOURCE_HASH)),)
$(error could not compute the Obscura wrapper source hash)
endif
OBSCURA_IMAGE ?= $(call env_value,OBSCURA_IMAGE)
ifeq ($(strip $(OBSCURA_IMAGE)),)
OBSCURA_IMAGE := $(OBSCURA_WRAPPER_IMAGE_REPOSITORY):$(OBSCURA_RELEASE_VERSION)-stealth-$(OBSCURA_WRAPPER_SOURCE_HASH)
endif
OBSCURA_IMAGE := $(OBSCURA_IMAGE)
ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB ?= $(call env_value,ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB)
ifeq ($(strip $(ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB)),)
ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB := 20
endif
ONYX_RAG_DOC_SOURCE_DIR ?= $(call env_value,ONYX_RAG_DOC_SOURCE_DIR)
ifeq ($(strip $(ONYX_RAG_DOC_SOURCE_DIR)),)
ONYX_RAG_DOC_SOURCE_DIR := ./doc-drop
endif
HOST_PORT_TEEP ?= $(call env_value,HOST_PORT_TEEP)
ifeq ($(strip $(HOST_PORT_TEEP)),)
HOST_PORT_TEEP := 8337
endif
PODMAN_DOC_SERVER_PORT := 18091
PODMAN_DOC_SERVER_STATE_DIR := docker-data/host-services
PODMAN_DOC_SERVER_PID_FILE := $(PODMAN_DOC_SERVER_STATE_DIR)/podman-doc-server.pid
PODMAN_DOC_SERVER_LOG := $(PODMAN_DOC_SERVER_STATE_DIR)/podman-doc-server.log
export PODMAN_DOC_SERVER_PORT
# The shared Obscura server must accommodate both the configurable built-in
# open_url document limit and SearXNG's independent fixed 20 MiB DOM limit.
OBSCURA_RETENTION_FLOOR_BYTES := $(shell python3 -c 'v=int("$(ONYX_OPEN_URL_MAX_DOCUMENT_SIZE_MB)"); assert 0 < v <= ((1<<63)-1)//1048576; print(max(v*1048576,20971520))')
OBSCURA_IO_STREAM_MAX_BYTES := $(OBSCURA_RETENTION_FLOOR_BYTES)
export SEARXNG_WRAPPER_IMAGE
export SEARXNG_WRAPPER_IMAGE_REPOSITORY
export SEARXNG_WRAPPER_SOURCE_HASH
export OBSCURA_IMAGE
export OBSCURA_WRAPPER_SOURCE_HASH
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
CODE_INTERPRETER_IMAGE_TAG := $(CODE_INTERPRETER_IMAGE_TAG)
CODE_INTERPRETER_IMAGE ?= docker.io/onyxdotapp/code-interpreter:$(CODE_INTERPRETER_IMAGE_TAG)
export CODE_INTERPRETER_IMAGE_TAG
PYTHON_EXECUTOR_IMAGE_TAG ?= $(call env_value,PYTHON_EXECUTOR_IMAGE_TAG)
ifeq ($(strip $(PYTHON_EXECUTOR_IMAGE_TAG)),)
$(error PYTHON_EXECUTOR_IMAGE_TAG is not set. Add it to $(VERSION_FILE) or override it on the make command line)
endif
PYTHON_EXECUTOR_UPSTREAM_IMAGE ?= $(call env_value,PYTHON_EXECUTOR_UPSTREAM_IMAGE)
ifeq ($(strip $(PYTHON_EXECUTOR_UPSTREAM_IMAGE)),)
$(error PYTHON_EXECUTOR_UPSTREAM_IMAGE is not set. Add it to $(VERSION_FILE) or override it on the make command line)
endif
PYTHON_EXECUTOR_DOCKERFILE ?= executor/Dockerfile
PYTHON_EXECUTOR_REQUIREMENTS_IN := executor/requirements.in
PYTHON_EXECUTOR_REQUIREMENTS := executor/requirements.txt
PYTHON_EXECUTOR_WRAPPER_IMAGE_REPOSITORY ?= $(call env_value,PYTHON_EXECUTOR_WRAPPER_IMAGE_REPOSITORY)
ifeq ($(strip $(PYTHON_EXECUTOR_WRAPPER_IMAGE_REPOSITORY)),)
$(error PYTHON_EXECUTOR_WRAPPER_IMAGE_REPOSITORY is not set. Add it to $(VERSION_FILE) or override it on the make command line)
endif
PYTHON_EXECUTOR_WRAPPER_BUILD_INPUTS := \
	$(PYTHON_EXECUTOR_DOCKERFILE) \
	$(PYTHON_EXECUTOR_REQUIREMENTS)
PYTHON_EXECUTOR_WRAPPER_SOURCE_HASH := $(shell python3 -c 'import hashlib,pathlib,sys; h=hashlib.sha256(sys.argv[1].encode()+b"\0"); [h.update(p.encode()+b"\0"+pathlib.Path(p).read_bytes()) for p in sys.argv[2:]]; print(h.hexdigest()[:12])' '$(PYTHON_EXECUTOR_UPSTREAM_IMAGE)' $(PYTHON_EXECUTOR_WRAPPER_BUILD_INPUTS))
ifeq ($(strip $(PYTHON_EXECUTOR_WRAPPER_SOURCE_HASH)),)
$(error could not compute the Python executor wrapper source hash)
endif
PYTHON_EXECUTOR_IMAGE ?= $(PYTHON_EXECUTOR_WRAPPER_IMAGE_REPOSITORY):$(PYTHON_EXECUTOR_IMAGE_TAG)-$(PYTHON_EXECUTOR_WRAPPER_SOURCE_HASH)
PYTHON_EXECUTOR_IMAGE := $(PYTHON_EXECUTOR_IMAGE)
export PYTHON_EXECUTOR_IMAGE
ifeq ($(PODMAN_SELECTED),true)
ONYX_STACK_REQUIRED_IMAGES := $(ONYX_BACKEND_IMAGE) $(ONYX_WEB_SERVER_IMAGE)
CODE_INTERPRETER_EXECUTOR_TARGETS :=
else
ONYX_STACK_REQUIRED_IMAGES := $(ONYX_BACKEND_IMAGE) $(ONYX_WEB_SERVER_IMAGE) $(CODE_INTERPRETER_IMAGE)
CODE_INTERPRETER_EXECUTOR_TARGETS := executor-image-ready
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
MYST_SIGNUP_PROJECT := private-onyx-myst-signup
MYST_DATA_DIR := docker-data/myst-data
MYST_VPN_ORDER_AMOUNT ?= $(call env_value,MYST_VPN_ORDER_AMOUNT)
MYST_VPN_ORDER_CURRENCY ?= $(call env_value,MYST_VPN_ORDER_CURRENCY)
MYST_VPN_ORDER_GATEWAY ?= $(call env_value,MYST_VPN_ORDER_GATEWAY)
MYST_VPN_ORDER_COUNTRY ?= $(call env_value,MYST_VPN_ORDER_COUNTRY)
MYST_VPN_ORDER_GATEWAY_DATA ?= $(call env_value,MYST_VPN_ORDER_GATEWAY_DATA)
ifeq ($(strip $(MYST_VPN_ORDER_AMOUNT)),)
MYST_VPN_ORDER_AMOUNT := 100
endif
ifeq ($(strip $(MYST_VPN_ORDER_CURRENCY)),)
MYST_VPN_ORDER_CURRENCY := MYST
endif
ifeq ($(strip $(MYST_VPN_ORDER_GATEWAY)),)
MYST_VPN_ORDER_GATEWAY := coingate
endif
ifeq ($(strip $(MYST_VPN_ORDER_COUNTRY)),)
MYST_VPN_ORDER_COUNTRY := US
endif
ifeq ($(strip $(MYST_VPN_ORDER_GATEWAY_DATA)),)
MYST_VPN_ORDER_GATEWAY_DATA := custom_id=mysterium-onyx
endif
MYST_VPN_IDENTITY ?= $(call env_value,MYST_VPN_IDENTITY)
EMBEDSERV_DIR := embedserv
HOST_PROCESS_MANAGER := $(EMBEDSERV_DIR)/host_process_manager.py
EMBEDSERV_REQUIREMENTS_IN := $(EMBEDSERV_DIR)/requirements.in
EMBEDSERV_REQUIREMENTS := $(EMBEDSERV_DIR)/requirements.txt
EMBEDSERV_VENV := $(EMBEDSERV_DIR)/.venv
EMBEDSERV_MODEL_CACHE := $(EMBEDSERV_DIR)/models
EMBEDSERV_LOG := $(EMBEDSERV_DIR)/serve.log
EMBEDSERV_PID_FILE := $(EMBEDSERV_DIR)/serve.pid
OPENSEARCH_VALIDATION_CONTAINER ?= onyx-opensearch-1
OPENSEARCH_VALIDATION_API_CONTAINER ?= onyx-api_server-1
OPENSEARCH_VALIDATION_DOCUMENTS ?= 500
OPENSEARCH_VALIDATION_VECTOR_DIMENSION ?= 128
OPENSEARCH_VALIDATION_CONCURRENCY ?= 4
OPENSEARCH_VALIDATION_ITERATIONS ?= 24
OPENSEARCH_EXPECTED_VERSION ?= 3.6.0
SEARXNG_REQUIREMENTS_IN := searxng/requirements.in
SEARXNG_REQUIREMENTS := searxng/requirements.txt
UV_CACHE_DIR ?= /tmp/private-onyx-uv-cache

LITE_FILES := $(WRAPPER_FILE):$(LITE_OVERRIDE_FILE)$(PODMAN_COMPOSE_SUFFIX)$(DOCKER_LINUX_COMPOSE_SUFFIX)$(TEEP_VPN_SUFFIX)$(TAILSCALE_VPN_SUFFIX)$(CODE_INTERPRETER_NETWORK_SUFFIX)$(TOR_SUFFIX)
FULL_FILES := $(WRAPPER_FILE):$(FULL_OVERRIDE_FILE)$(PODMAN_COMPOSE_SUFFIX)$(PODMAN_FULL_COMPOSE_SUFFIX)$(PODMAN_MACOS_FULL_COMPOSE_SUFFIX)$(DOCKER_LINUX_COMPOSE_SUFFIX)$(TEEP_VPN_SUFFIX)$(TAILSCALE_VPN_SUFFIX)$(CODE_INTERPRETER_NETWORK_SUFFIX)$(TOR_SUFFIX)
LITE_DOWN_FILES := $(WRAPPER_FILE):$(LITE_OVERRIDE_FILE)$(PODMAN_COMPOSE_SUFFIX)$(DOCKER_LINUX_COMPOSE_SUFFIX)$(TEEP_VPN_SUFFIX)$(TAILSCALE_VPN_SUFFIX)$(CODE_INTERPRETER_NETWORK_SUFFIX)$(TOR_DOWN_SUFFIX)
FULL_DOWN_FILES := $(WRAPPER_FILE):$(FULL_OVERRIDE_FILE)$(PODMAN_COMPOSE_SUFFIX)$(PODMAN_FULL_COMPOSE_SUFFIX)$(PODMAN_MACOS_FULL_COMPOSE_SUFFIX)$(DOCKER_LINUX_COMPOSE_SUFFIX)$(TEEP_VPN_SUFFIX)$(TAILSCALE_VPN_SUFFIX)$(CODE_INTERPRETER_NETWORK_SUFFIX)$(TOR_DOWN_SUFFIX)

# Lite mode has no wrapper-owned host services. Full mode always reconciles the
# optional bundled MLX service, but selects the host document server only for
# Podman; Docker serves documents from its container bind mount.
FULL_MODE_HOST_PROCESS_TARGETS := embedserv-start-if-installed
EMBEDDING_STAGE_SERVICES := teep $(TEEP_EMBEDDING_HOST_PUBLISHER) local-embedding-shim
ifeq ($(PODMAN_SELECTED),true)
ifeq ($(HOST_OS),Darwin)
FULL_MODE_HOST_PROCESS_TARGETS += podman-doc-server-start
else
FULL_MODE_HOST_PROCESS_TARGETS += podman-doc-server-stop-if-started
endif
endif

.PHONY: help test check test-patch-images test-obscura-image test-tor-image test-opensearch-image test-all-images check-upgrade integration-opensearch integration-opensearch-restart integration-opensearch-onyx health-inventory shared-data-engine-status claim-shared-data-engine adopt-shared-data-engine release-shared-data-engine prepare-lite-host-data prepare-full-host-data up-lite up-full down-lite down-full ps-lite ps-full logs-lite logs-full check-container-health-capability prepare-podman-postgres-data prepare-podman-opensearch-data podman-doc-server-start podman-doc-server-stop-if-started embedding-ready-once ensure-onyx-config init-onyx-env sync-onyx-env upgrade upgrade-onyx upgrade-python-deps searxng-image-ready searxng-build executor-image-ready executor-build obscura-image-ready obscura-build tailscale-image-ready wrapper-config-preflight tor-config-ready tor-image-ready tor-build tor-onion-address myst-image-ready myst-build teep-image-ready teep-build onyx-image-ready onyx-build embedserv-install embedserv-verify-model embedserv-start-if-installed embedserv-stop-if-started embedserv-stop-after-custom-ready vpn-signup-orderform vpn-signup-blockchain vpn-signup-stop vpn-orderstatus vpn-balance vpn-connection-info ensure-myst-funded

.NOTPARALLEL: up-lite up-full

help:
	@echo "Lite mode (use these commands together):"
	@echo "  make up-lite                # Start wrapper + Onyx lite"
	@echo "  make down-lite              # Stop wrapper + Onyx lite"
	@echo "  make ps-lite                # Show lite mode containers"
	@echo "  make logs-lite              # Tail lite mode logs"
	@echo ""
	@echo "Full mode (use these commands together):"
	@echo "  make up-full                # Start full mode and the installed default MLX lifecycle proxy"
	@echo "  make down-full              # Stop wrapper + Onyx full"
	@echo "  make ps-full                # Show full mode containers"
	@echo "  make logs-full              # Tail full mode logs"
	@echo ""
	@echo "Optional Local Mac Embedding Setup (For Full-Mode RAG):"
	@echo "  make embedserv-install      # Create embedserv venv, download the MLX model, and verify it"
	@echo "  make embedserv-verify-model # Re-verify an already installed MLX embedding model"
	@echo ""
	@echo "Optional Tor Onion Service (requires TOR_ONION_SERVICE_ENABLED=true):"
	@echo "  make tor-onion-address      # Print the running onion service address"
	@echo ""
	@echo "Optional VPN setup and payment (requries MYST_VPN_ENABLED=true):"
	@echo "  make vpn-signup-orderform   # Start standalone Myst container, create identity + CoinGate order, show payment URL"
	@echo "  make vpn-signup-blockchain  # Start standalone Myst container, create identity, show channel address for direct MYST transfer"
	@echo "  make vpn-signup-stop        # Stop the standalone Myst signup container"
	@echo "  make vpn-orderstatus        # Show balance, order status, and payment URL"
	@echo "  make vpn-balance            # Quick balance check"
	@echo "  make vpn-connection-info    # Show the active Myst connection and provider"
	@echo ""
	@echo "Development and maintenance:"
	@echo "  make test                      # Run the complete deterministic Python test suite"
	@echo "  make check                     # Run the normal deterministic pre-handoff checks"
	@echo "  make upgrade                   # Upgrade Python locks, pull changed image tags, rebuild containers, run Onyx deployment"
	@echo "  make check-upgrade             # Run broad post-upgrade deterministic and image validation"
	@echo "  make adopt-shared-data-engine  # Repair Docker vs Podman data-dir owner based on CONTAINER_BIN env value"
	@echo ""
	@echo "Configuration:"
	@echo "  Version manifest: $(VERSION_FILE) (override with VERSION_FILE=...)"
	@echo "  Override env file: make up-lite ENV_FILE=.env.wrapper"
	@echo "  Enable VPN: set MYST_VPN_ENABLED=true in $(ENV_FILE) to enable Myst VPN setup + egress"
	@echo "  Proxy: set EGRESS_UPSTREAM_PROXY_URL in $(ENV_FILE) (http/https/socks5)"
	@echo "  Tor: set TOR_EGRESS_ENABLED=true and/or TOR_ONION_SERVICE_ENABLED=true"
	@echo "       then use 'make tor-onion-address' to print a running onion address"

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
		$(HOST_PROCESS_MANAGER) \
		embedserv/idle_embedding_proxy.py \
		podman \
		tor \
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
	@$(SHARED_DATA_GUARD_ENV) python3 podman/shared_data_engine.py claim --engine "$(SHARED_DATA_ENGINE)" --container-bin "$(CONTAINER_BIN)" --marker "$(SHARED_DATA_ENGINE_MARKER)"

adopt-shared-data-engine:
	@$(SHARED_DATA_GUARD_ENV) python3 podman/shared_data_engine.py claim --engine "$(SHARED_DATA_ENGINE)" --container-bin "$(CONTAINER_BIN)" --marker "$(SHARED_DATA_ENGINE_MARKER)" --adopt-unclaimed

release-shared-data-engine:
	@python3 podman/shared_data_engine.py release --engine "$(SHARED_DATA_ENGINE)" --marker "$(SHARED_DATA_ENGINE_MARKER)"

prepare-lite-host-data:
	@python3 podman/startup_health.py prepare-host-directories \
		--data-root docker-data \
		--mode lite

prepare-full-host-data:
	@python3 podman/startup_health.py prepare-host-directories \
		--data-root docker-data \
		--mode full \
		--doc-source "$(ONYX_RAG_DOC_SOURCE_DIR)" \
		--default-doc-source ./doc-drop

test-patch-images:
	@CONTAINER_BIN="$(CONTAINER_BIN)" \
		ONYX_BACKEND_IMAGE="$(ONYX_BACKEND_IMAGE)" \
		ONYX_WEB_SERVER_IMAGE="$(ONYX_WEB_SERVER_IMAGE)" \
		CODE_INTERPRETER_IMAGE="$(CODE_INTERPRETER_IMAGE)" \
		PYTHON_EXECUTOR_IMAGE="$(PYTHON_EXECUTOR_IMAGE)" \
		SEARXNG_WRAPPER_IMAGE="$(SEARXNG_WRAPPER_IMAGE)" \
		./tests/validate_pinned_patch_images.sh

test-obscura-image:
	@if ! "$(CONTAINER_BIN)" image inspect "$(OBSCURA_IMAGE)" >/dev/null 2>&1; then \
		echo "ERROR: Obscura image is missing: $(OBSCURA_IMAGE)" >&2; \
		echo "Run 'make obscura-image-ready CONTAINER_BIN=$(CONTAINER_BIN)' first." >&2; \
		exit 1; \
	fi
	@if ! "$(CONTAINER_BIN)" image inspect "$(ONYX_BACKEND_IMAGE)" >/dev/null 2>&1; then \
		echo "ERROR: Onyx backend image is missing: $(ONYX_BACKEND_IMAGE)" >&2; \
		echo "Run 'make onyx-image-ready CONTAINER_BIN=$(CONTAINER_BIN)' first." >&2; \
		exit 1; \
	fi
	@if ! "$(CONTAINER_BIN)" image inspect "$(PYTHON_ALPINE_IMAGE)" >/dev/null 2>&1; then \
		echo "ERROR: fixture image is missing: $(PYTHON_ALPINE_IMAGE)" >&2; \
		echo "Pull the pinned fixture image before validation." >&2; \
		exit 1; \
	fi
	@python3 tests/validate_obscura_image.py \
		--container-bin "$(CONTAINER_BIN)" \
		--image "$(OBSCURA_IMAGE)" \
		--client-image "$(ONYX_BACKEND_IMAGE)" \
		--fixture-image "$(PYTHON_ALPINE_IMAGE)"

test-tor-image:
	@if ! "$(CONTAINER_BIN)" image inspect "$(TOR_IMAGE)" >/dev/null 2>&1; then \
		echo "ERROR: Tor wrapper image is missing: $(TOR_IMAGE)" >&2; \
		echo "Run 'make tor-build CONTAINER_BIN=$(CONTAINER_BIN)' first." >&2; \
		exit 1; \
	fi
	@python3 tests/validate_tor_image.py \
		--engine "$(CONTAINER_BIN)" \
		--image "$(TOR_IMAGE)" \
		--base-image "$(TOR_BASE_IMAGE)"

test-opensearch-image:
	@set -eu; \
	image=$$(COMPOSE_FILE="$(FULL_FILES)" "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) config --format json \
		| python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["opensearch"]["image"])'); \
	"$(CONTAINER_BIN)" image inspect "$$image" >/dev/null; \
	python3 tests/run_opensearch_image_validation.py \
		--container-bin "$(CONTAINER_BIN)" \
		--image "$$image" \
		--audit-config onyx/opensearch/audit.yml \
		--documents "$(OPENSEARCH_VALIDATION_DOCUMENTS)" \
		--vector-dimension "$(OPENSEARCH_VALIDATION_VECTOR_DIMENSION)" \
		--concurrency "$(OPENSEARCH_VALIDATION_CONCURRENCY)" \
		--iterations "$(OPENSEARCH_VALIDATION_ITERATIONS)" \
		--expected-version "$(OPENSEARCH_EXPECTED_VERSION)"

test-all-images:
	@$(MAKE) --no-print-directory test-patch-images
	@$(MAKE) --no-print-directory test-obscura-image
	@$(MAKE) --no-print-directory test-tor-image
	@$(MAKE) --no-print-directory test-opensearch-image

integration-opensearch:
	@python3 tests/opensearch_runtime_validation.py \
		--container-bin "$(CONTAINER_BIN)" \
		--container "$(OPENSEARCH_VALIDATION_CONTAINER)" \
		--documents "$(OPENSEARCH_VALIDATION_DOCUMENTS)" \
		--vector-dimension "$(OPENSEARCH_VALIDATION_VECTOR_DIMENSION)" \
		--concurrency "$(OPENSEARCH_VALIDATION_CONCURRENCY)" \
		--iterations "$(OPENSEARCH_VALIDATION_ITERATIONS)" \
		--expected-version "$(OPENSEARCH_EXPECTED_VERSION)"

integration-opensearch-restart:
	@python3 tests/opensearch_runtime_validation.py \
		--container-bin "$(CONTAINER_BIN)" \
		--container "$(OPENSEARCH_VALIDATION_CONTAINER)" \
		--documents "$(OPENSEARCH_VALIDATION_DOCUMENTS)" \
		--vector-dimension "$(OPENSEARCH_VALIDATION_VECTOR_DIMENSION)" \
		--concurrency "$(OPENSEARCH_VALIDATION_CONCURRENCY)" \
		--iterations "$(OPENSEARCH_VALIDATION_ITERATIONS)" \
		--expected-version "$(OPENSEARCH_EXPECTED_VERSION)" \
		--restart

integration-opensearch-onyx:
	@python3 tests/onyx_opensearch_runtime_validation.py \
		--container-bin "$(CONTAINER_BIN)" \
		--api-container "$(OPENSEARCH_VALIDATION_API_CONTAINER)"

check-upgrade:
	@$(MAKE) --no-print-directory check
	@$(MAKE) --no-print-directory test-all-images

upgrade: upgrade-python-deps myst-build teep-build searxng-build executor-build tor-build tailscale-image-ready obscura-image-ready upgrade-onyx
	@echo "Upgrade artifacts are ready. Run 'make check-upgrade', then complete the documented live validation matrix."

upgrade-python-deps:
	@set -eu; \
	if ! command -v uv >/dev/null 2>&1; then \
		echo "ERROR: uv is required for upgrade-python-deps"; \
		exit 1; \
	fi; \
	UV_CACHE_DIR="$(UV_CACHE_DIR)" uv pip compile --upgrade --generate-hashes "$(EMBEDSERV_REQUIREMENTS_IN)" -o "$(EMBEDSERV_REQUIREMENTS)"; \
	UV_CACHE_DIR="$(UV_CACHE_DIR)" uv pip compile --upgrade --generate-hashes "$(SEARXNG_REQUIREMENTS_IN)" -o "$(SEARXNG_REQUIREMENTS)"; \
	UV_CACHE_DIR="$(UV_CACHE_DIR)" uv pip compile --upgrade --generate-hashes "$(PYTHON_EXECUTOR_REQUIREMENTS_IN)" -o "$(PYTHON_EXECUTOR_REQUIREMENTS)"

tailscale-image-ready:
	@echo "Pulling Tailscale image: $(TAILSCALE_IMAGE)"; \
	"$(CONTAINER_BIN)" pull "$(TAILSCALE_IMAGE)"

obscura-image-ready:
	@if "$(CONTAINER_BIN)" image inspect "$(OBSCURA_IMAGE)" >/dev/null 2>&1; then \
		echo "obscura stealth image already present: $(OBSCURA_IMAGE)"; \
	else \
		echo "obscura stealth image not found: $(OBSCURA_IMAGE). Building..."; \
		$(MAKE) obscura-build OBSCURA_IMAGE="$(OBSCURA_IMAGE)"; \
	fi

obscura-build:
	@echo "Building $(OBSCURA_IMAGE) from pinned Obscura $(OBSCURA_RELEASE_VERSION) stealth release..."
	@set -eu; set --; \
	[ -z "$${HTTP_PROXY:-}" ] || set -- "$$@" --build-arg HTTP_PROXY; \
	[ -z "$${HTTPS_PROXY:-}" ] || set -- "$$@" --build-arg HTTPS_PROXY; \
	[ -z "$${NO_PROXY:-}" ] || set -- "$$@" --build-arg NO_PROXY; \
	[ -z "$${http_proxy:-}" ] || set -- "$$@" --build-arg http_proxy; \
	[ -z "$${https_proxy:-}" ] || set -- "$$@" --build-arg https_proxy; \
	[ -z "$${no_proxy:-}" ] || set -- "$$@" --build-arg no_proxy; \
	"$(CONTAINER_BIN)" build "$$@" \
		--file "$(OBSCURA_DOCKERFILE)" \
		--build-arg PYTHON_ALPINE_IMAGE="$(PYTHON_ALPINE_IMAGE)" \
		--build-arg OBSCURA_UPSTREAM_IMAGE="$(OBSCURA_UPSTREAM_IMAGE)" \
		--build-arg OBSCURA_RELEASE_VERSION="$(OBSCURA_RELEASE_VERSION)" \
		--build-arg OBSCURA_RELEASE_AMD64_SHA256="$(OBSCURA_RELEASE_AMD64_SHA256)" \
		--build-arg OBSCURA_RELEASE_ARM64_SHA256="$(OBSCURA_RELEASE_ARM64_SHA256)" \
		--tag "$(OBSCURA_IMAGE)" \
		.

wrapper-config-preflight:
	@python3 tor/render_config.py validate \
		--settings-file "$(ENV_FILE)"

tor-image-ready:
	@if [ "$(TOR_EGRESS_ENABLED)" != "true" ] && [ "$(TOR_ONION_SERVICE_ENABLED)" != "true" ]; then exit 0; fi; \
	if "$(CONTAINER_BIN)" image inspect "$(TOR_IMAGE)" >/dev/null 2>&1; then \
		echo "Tor wrapper image already present: $(TOR_IMAGE)"; \
	else \
		$(MAKE) tor-build; \
	fi

tor-build:
	@echo "Pulling immutable Tor base: $(TOR_BASE_IMAGE)"
	@"$(CONTAINER_BIN)" pull "$(TOR_BASE_IMAGE)"
	@"$(CONTAINER_BIN)" build \
		--file "$(TOR_DOCKERFILE)" \
		--build-arg TOR_BASE_IMAGE="$(TOR_BASE_IMAGE)" \
		--tag "$(TOR_IMAGE)" \
		tor

tor-config-ready:
	@if [ "$(TOR_EGRESS_ENABLED)" != "true" ] && [ "$(TOR_ONION_SERVICE_ENABLED)" != "true" ]; then exit 0; fi; \
	mkdir -p docker-data/tor/config docker-data/tor/state; \
	chmod 0700 docker-data/tor/state; \
	python3 tor/render_config.py render \
		--settings-file "$(ENV_FILE)" \
		--output docker-data/tor/config/torrc

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

executor-image-ready:
	@if "$(CONTAINER_BIN)" image inspect "$(PYTHON_EXECUTOR_IMAGE)" >/dev/null 2>&1; then \
		echo "Python executor wrapper image already present: $(PYTHON_EXECUTOR_IMAGE)"; \
	else \
		$(MAKE) executor-build; \
	fi

executor-build:
	@echo "Building $(PYTHON_EXECUTOR_IMAGE) using $(PYTHON_EXECUTOR_DOCKERFILE)..."
	@set -eu; set --; \
	[ -z "$${HTTP_PROXY:-}" ] || set -- "$$@" --build-arg HTTP_PROXY; \
	[ -z "$${HTTPS_PROXY:-}" ] || set -- "$$@" --build-arg HTTPS_PROXY; \
	[ -z "$${NO_PROXY:-}" ] || set -- "$$@" --build-arg NO_PROXY; \
	[ -z "$${http_proxy:-}" ] || set -- "$$@" --build-arg http_proxy; \
	[ -z "$${https_proxy:-}" ] || set -- "$$@" --build-arg https_proxy; \
	[ -z "$${no_proxy:-}" ] || set -- "$$@" --build-arg no_proxy; \
	"$(CONTAINER_BIN)" build "$$@" \
		--file "$(PYTHON_EXECUTOR_DOCKERFILE)" \
		--build-arg PYTHON_EXECUTOR_UPSTREAM_IMAGE="$(PYTHON_EXECUTOR_UPSTREAM_IMAGE)" \
		--tag "$(PYTHON_EXECUTOR_IMAGE)" \
		.

up-lite: ONYX_INSTALL_ARGS=--lite
up-lite: ONYX_REQUIRED_IMAGES=$(ONYX_STACK_REQUIRED_IMAGES)
up-lite: PODMAN_START_FILES=$(LITE_FILES)
check-container-health-capability:
	@set -eu; \
	python3 podman/startup_health.py check-compose --container-bin "$(CONTAINER_BIN)"; \
	case "$(CONTAINER_BIN)" in \
		*podman*) \
			if [ -z "$(DOCKER_SOCK_PATH)" ] || [ ! -S "$(DOCKER_SOCK_PATH)" ]; then \
				echo "ERROR: Podman Compose API socket is unavailable at $(DOCKER_SOCK_PATH)" >&2; \
				echo "On native Linux, run: systemctl --user enable --now podman.socket" >&2; \
				echo "On macOS, start the selected Podman machine." >&2; \
				exit 1; \
			fi; \
			exec python3 podman/startup_health.py check --container-bin "$(CONTAINER_BIN)" \
			;; \
	esac; \
	engine_api="$$($(CONTAINER_BIN) version --format '{{.Server.APIVersion}}')"; \
	ENGINE_API="$$engine_api" python3 -c 'import os; from re import findall; parts=tuple((list(map(int, findall(r"\d+", os.environ["ENGINE_API"])))+[0,0])[:2]); assert parts >= (1,44), "Docker Engine API 1.44+ is required for start_interval"'

podman-doc-server-start:
ifeq ($(PODMAN_SELECTED),true)
	@set -eu; \
	source_dir="$(abspath $(ONYX_RAG_DOC_SOURCE_DIR))"; \
	default_source_dir="$(abspath ./doc-drop)"; \
	server_script="$(CURDIR)/onyx/doc_drop_webserver.py"; \
	if [ ! -e "$$source_dir" ] && [ "$$source_dir" = "$$default_source_dir" ]; then \
		mkdir -- "$$source_dir"; \
	fi; \
	if [ ! -d "$$source_dir" ]; then \
		echo "ERROR: configured RAG document source is not a directory"; \
		exit 1; \
	fi; \
	echo "Starting loopback-peer-restricted Podman host document server on port $(PODMAN_DOC_SERVER_PORT)"; \
	exec python3 "$(PWD)/$(HOST_PROCESS_MANAGER)" start \
		--name "Podman host document server" \
		--record-file "$(PODMAN_DOC_SERVER_PID_FILE)" \
		--log-file "$(PODMAN_DOC_SERVER_LOG)" \
		--identity "$$server_script" \
		--fingerprint-file "$$server_script" \
		--port "$(PODMAN_DOC_SERVER_PORT)" \
		--health-path /_health \
		--startup-timeout 10 \
		--stop-timeout 10 \
		-- python3 "$$server_script" "$(PODMAN_DOC_SERVER_PORT)" --bind 0.0.0.0 --directory "$$source_dir" --loopback-peers-only
endif

podman-doc-server-stop-if-started:
	@if [ ! -e "$(PODMAN_DOC_SERVER_PID_FILE)" ]; then exit 0; fi; \
	python3 "$(PWD)/$(HOST_PROCESS_MANAGER)" stop \
		--name "Podman host document server" \
		--record-file "$(PODMAN_DOC_SERVER_PID_FILE)" \
		--identity "$(CURDIR)/onyx/doc_drop_webserver.py" \
		--stop-timeout 10

prepare-podman-postgres-data:
ifeq ($(PODMAN_SELECTED),true)
	@COMPOSE_FILE=$(PODMAN_START_FILES) python3 podman/startup_health.py initialize-postgres \
		--container-bin "$(CONTAINER_BIN)" --postgres docker-data/postgres $(ONYX_COMPOSE_ENV_FILES)
endif

prepare-podman-opensearch-data:
ifeq ($(PODMAN_SELECTED),true)
	@COMPOSE_FILE=$(PODMAN_START_FILES) python3 podman/startup_health.py initialize-opensearch \
		--container-bin "$(CONTAINER_BIN)" --opensearch docker-data/opensearch $(ONYX_COMPOSE_ENV_FILES)
endif

up-lite: wrapper-config-preflight check-container-health-capability claim-shared-data-engine prepare-lite-host-data ensure-onyx-config sync-onyx-env onyx-image-ready prepare-podman-postgres-data ensure-myst-funded $(CODE_INTERPRETER_EXECUTOR_TARGETS) myst-image-ready teep-image-ready searxng-image-ready obscura-image-ready tor-image-ready tor-config-ready
ifeq ($(PODMAN_SELECTED),true)
	@COMPOSE_FILE=$(LITE_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) up --no-start --no-deps relational_db
ifneq ($(filter true,$(TOR_EGRESS_ENABLED) $(TOR_ONION_SERVICE_ENABLED)),)
	@COMPOSE_FILE=$(LITE_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) up --no-start --no-deps tor
endif
	@COMPOSE_FILE=$(LITE_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) up --no-start --no-deps netns-holder
	@COMPOSE_FILE=$(LITE_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) up --no-start --no-deps --force-recreate onyx-host-egress-proxy onyx-host-egress-bridge
	@COMPOSE_FILE=$(LITE_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) create
	@COMPOSE_FILE=$(LITE_FILES) python3 podman/startup_health.py configure --skip-capability-check --container-bin "$(CONTAINER_BIN)" --project onyx $(ONYX_COMPOSE_ENV_FILES)
endif
	@COMPOSE_FILE=$(LITE_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) up -d --wait --wait-timeout 420
ifeq ($(PODMAN_SELECTED),true)
	@python3 podman/startup_health.py assert-healthy --container-bin "$(CONTAINER_BIN)" --project onyx \
		--service onyx-host-egress-bridge
endif

up-full: ONYX_INSTALL_ARGS=
up-full: ONYX_REQUIRED_IMAGES=$(ONYX_STACK_REQUIRED_IMAGES)
up-full: PODMAN_START_FILES=$(FULL_FILES)
up-full: wrapper-config-preflight check-container-health-capability claim-shared-data-engine prepare-full-host-data ensure-onyx-config sync-onyx-env onyx-image-ready prepare-podman-postgres-data prepare-podman-opensearch-data ensure-myst-funded $(CODE_INTERPRETER_EXECUTOR_TARGETS) myst-image-ready teep-image-ready searxng-image-ready obscura-image-ready tor-image-ready tor-config-ready $(FULL_MODE_HOST_PROCESS_TARGETS)
ifeq ($(PODMAN_SELECTED),true)
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) up --no-start --no-deps relational_db
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) up --no-start --no-deps opensearch
ifneq ($(filter true,$(TOR_EGRESS_ENABLED) $(TOR_ONION_SERVICE_ENABLED)),)
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) up --no-start --no-deps tor
endif
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) up --no-start --no-deps netns-holder
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) up --no-start --no-deps --force-recreate onyx-host-egress-proxy onyx-host-egress-bridge
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) create $(EMBEDDING_STAGE_SERVICES)
	@COMPOSE_FILE=$(FULL_FILES) python3 podman/startup_health.py configure --skip-capability-check --container-bin "$(CONTAINER_BIN)" --project onyx $(ONYX_COMPOSE_ENV_FILES)
endif
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) up -d --wait --wait-timeout 420 $(EMBEDDING_STAGE_SERVICES)
ifeq ($(PODMAN_SELECTED),true)
	@python3 podman/startup_health.py assert-healthy --container-bin "$(CONTAINER_BIN)" --project onyx \
		--service onyx-host-egress-bridge
endif
	@$(MAKE) --no-print-directory embedding-ready-once
	@$(MAKE) --no-print-directory embedserv-stop-after-custom-ready
ifeq ($(PODMAN_SELECTED),true)
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) create
	@COMPOSE_FILE=$(FULL_FILES) python3 podman/startup_health.py configure --skip-capability-check --container-bin "$(CONTAINER_BIN)" --project onyx $(ONYX_COMPOSE_ENV_FILES)
endif
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) up -d --wait --wait-timeout 420

embedding-ready-once:
	@if [ "$(EMBEDSERV_MODE)" = "bundled" ]; then \
		echo "Loading and validating the bundled MLX embedding upstream; cold starts can take several minutes."; \
		echo "Bundled MLX startup details: $(EMBEDSERV_LOG)"; \
	else \
		echo "Loading and validating the configured custom embedding upstream."; \
	fi
	@echo "Full API/background startup will wait here. Press Ctrl-C to stop waiting."
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) exec -T local-embedding-shim \
		python3 -c 'import urllib.request; response = urllib.request.urlopen("http://127.0.0.1:9101/ready", timeout=None); raise SystemExit(0 if response.status == 200 else 1)'
	@echo "Configured embedding upstream is ready."

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
	@COMPOSE_PROFILES=tailscale COMPOSE_FILE=$(LITE_DOWN_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) down --remove-orphans
	@$(MAKE) --no-print-directory release-shared-data-engine

down-full:
	@COMPOSE_PROFILES=tailscale COMPOSE_FILE=$(FULL_DOWN_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) down --remove-orphans
	@"$${MAKE:-make}" podman-doc-server-stop-if-started
	@"$${MAKE:-make}" embedserv-stop-if-started
	@$(MAKE) --no-print-directory release-shared-data-engine

ps-lite:
	@COMPOSE_FILE=$(LITE_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) ps

ps-full:
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) ps

logs-lite:
	@COMPOSE_FILE=$(LITE_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) logs -f --tail=200

logs-full:
	@COMPOSE_FILE=$(FULL_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) logs -f --tail=200

tor-onion-address: wrapper-config-preflight
	@set -eu; \
	if [ "$(TOR_ONION_SERVICE_ENABLED)" != "true" ]; then \
		echo "ERROR: TOR_ONION_SERVICE_ENABLED must be true" >&2; exit 1; \
	fi; \
	container_id=$$(COMPOSE_FILE=$(LITE_FILES) "$(CONTAINER_BIN)" compose $(ONYX_COMPOSE_ENV_FILES) ps -q tor); \
	if [ -z "$$container_id" ] || [ "$$("$(CONTAINER_BIN)" inspect --format '{{.State.Running}}' "$$container_id" 2>/dev/null)" != "true" ]; then \
		echo "ERROR: Tor is not running; inspect make ps-lite/ps-full and Tor logs" >&2; exit 1; \
	fi; \
	address=$$("$(CONTAINER_BIN)" exec "$$container_id" sh -c 'test -f /var/lib/tor/onion-service/hostname && cat /var/lib/tor/onion-service/hostname'); \
	if [ -z "$$address" ] || [ "$$(printf '%s\n' "$$address" | wc -l | tr -d ' ')" -ne 1 ]; then \
		echo "ERROR: Tor onion address is not ready or is unsafe" >&2; exit 1; \
	fi; \
	echo "Onion address: $$address"

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

embedserv-install: wrapper-config-preflight
	@set -eu; \
	if ! command -v uv >/dev/null 2>&1; then \
		echo "ERROR: uv is required for embedserv-install"; \
		exit 1; \
	fi; \
	model_repo="$${ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL}"; \
	venv_python="$(PWD)/$(EMBEDSERV_VENV)/bin/python"; \
	model_dir="$(PWD)/$(EMBEDSERV_MODEL_CACHE)/$$model_repo"; \
	mkdir -p "$(EMBEDSERV_DIR)" "$$(dirname "$$model_dir")"; \
	if [ ! -x "$$venv_python" ]; then \
		uv venv --python 3.13 "$(EMBEDSERV_VENV)"; \
	fi; \
	uv pip install --python "$$venv_python" --require-hashes -r "$(EMBEDSERV_REQUIREMENTS)"; \
	echo "Downloading MLX embedding model: $$model_repo"; \
	MODEL_REPO="$$model_repo" MODEL_DIR="$$model_dir" "$$venv_python" -c 'import os; from huggingface_hub import snapshot_download; snapshot_download(repo_id=os.environ["MODEL_REPO"], local_dir=os.environ["MODEL_DIR"])'; \
	$(MAKE) --no-print-directory embedserv-verify-model; \
	echo "Model ready and verified at $$model_dir"

embedserv-verify-model: wrapper-config-preflight
	@set -eu; \
	model_repo="$${ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL}"; \
	model_dir="$(PWD)/$(EMBEDSERV_MODEL_CACHE)/$$model_repo"; \
	verifier="$(PWD)/$(EMBEDSERV_VENV)/bin/hf"; \
	if [ ! -x "$$verifier" ] || [ ! -d "$$model_dir" ]; then \
		echo "ERROR: the local embedding model or its verifier is not installed for $$model_repo."; \
		echo "Run 'make embedserv-install' first; installation downloads and verifies the model."; \
		exit 1; \
	fi; \
	echo "Verifying served model directory: $$model_dir"; \
	verify_out_file=$$(mktemp); \
	if "$$verifier" cache verify "$$model_repo" \
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

# Start the bundled host MLX server for full mode only after its selected model
# has been installed and only while the shim still targets the bundled default
# endpoint. Custom endpoints (including Teep) remain entirely operator-owned.
# A custom transition defers identity-checked cleanup until the new policy and
# embedding readiness have succeeded.
embedserv-start-if-installed:
	@set -eu; \
	model_repo="$${ONYX_RAG_EMBEDDING_MLX_SERVE_MODEL}"; \
	served_model="$${ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL}"; \
	embeddings_url="$${ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL}"; \
	venv_server="$(PWD)/$(EMBEDSERV_VENV)/bin/mlx-openai-server"; \
	model_dir="$(PWD)/$(EMBEDSERV_MODEL_CACHE)/$$model_repo"; \
	proxy_script="$(PWD)/$(EMBEDSERV_DIR)/idle_embedding_proxy.py"; \
	if { [ "$(EMBEDSERV_MODE)" = "bundled" ] && [ "$$embeddings_url" != "$(EMBEDSERV_DEFAULT_UPSTREAM_URL)" ]; } || \
	   { [ "$(EMBEDSERV_MODE)" = "custom" ] && [ "$$embeddings_url" = "$(EMBEDSERV_DEFAULT_UPSTREAM_URL)" ]; }; then \
		echo "ERROR: embedding lifecycle mode disagrees with the canonical upstream URL"; \
		exit 1; \
	fi; \
	if [ "$(EMBEDSERV_MODE)" = "custom" ]; then \
		if [ "$$embeddings_url" = "http://host.docker.internal:$(HOST_PORT_TEEP)/v1/embeddings" ] && \
		   [ "$$served_model" = "$$model_repo" ]; then \
			echo "ERROR: Teep embedding is selected, but ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL is unset."; \
			echo 'Set ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL="neardirect:Qwen/Qwen3-Embedding-0.6B" in $(ENV_FILE).'; \
			exit 1; \
		fi; \
		echo "Embedding shim uses a custom upstream; not starting bundled MLX server: $$embeddings_url"; \
		exit 0; \
	fi; \
	if [ ! -x "$$venv_server" ] || [ ! -d "$$model_dir" ]; then \
		echo "ERROR: full mode has no usable embedding endpoint."; \
		echo "The default endpoint uses the bundled MLX server, but it is not installed for model $$model_repo."; \
		echo "Choose one of these embedding setups:"; \
		echo "  1. On macOS, run: make embedserv-install"; \
		echo "  2. Configure Teep in $(ENV_FILE) (recommended non-Mac option):"; \
		echo '       ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL="http://host.docker.internal:8337/v1/embeddings"'; \
		echo '       ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL="neardirect:Qwen/Qwen3-Embedding-0.6B"'; \
		echo "     Replace port 8337 if HOST_PORT_TEEP is configured differently."; \
		echo "  3. Configure a custom OpenAI-compatible /v1/embeddings endpoint with"; \
		echo "     ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL and ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_MODEL"; \
		echo "     (and ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_API_KEY when required)."; \
		echo "See README.md or docs/local_docs_rag_search.md for full embedding setup instructions."; \
		exit 1; \
	fi; \
	echo "Starting bundled MLX embedding lifecycle proxy for $$model_repo (log: $(EMBEDSERV_LOG))"; \
	exec python3 "$(PWD)/$(HOST_PROCESS_MANAGER)" start \
		--name "MLX embedding lifecycle proxy" \
		--record-file "$(EMBEDSERV_PID_FILE)" \
		--log-file "$(EMBEDSERV_LOG)" \
		--identity "$$proxy_script" \
		--fingerprint-file "$$proxy_script" \
		--port 3210 \
		--startup-timeout 120 \
		--stop-timeout 50 \
		--truncate-log \
		--require-executable "$$venv_server" \
		--require-directory "$$model_dir" \
		-- python3 "$$proxy_script" \
		--listen-port 3210 \
		--child-port 3211 \
		--server-executable "$$venv_server" \
		--model-path "$$model_dir" \
		--served-model-name "$$served_model"

# Stop only the lifecycle proxy recorded by the automatic full-mode startup. An
# absent, exited, malformed, or reused PID is a diagnosed no-op so down-full is
# not made brittle by stale host state.
embedserv-stop-if-started:
	@if [ ! -e "$(EMBEDSERV_PID_FILE)" ]; then exit 0; fi; \
	python3 "$(PWD)/$(HOST_PROCESS_MANAGER)" stop \
		--name "MLX embedding lifecycle proxy" \
		--record-file "$(EMBEDSERV_PID_FILE)" \
		--identity "$(PWD)/embedserv/idle_embedding_proxy.py" \
		--stop-timeout 50

embedserv-stop-after-custom-ready:
	@set -eu; \
	embeddings_url="$${ONYX_RAG_EMBEDDING_SHIM_UPSTREAM_URL}"; \
	if { [ "$(EMBEDSERV_MODE)" = "bundled" ] && [ "$$embeddings_url" != "$(EMBEDSERV_DEFAULT_UPSTREAM_URL)" ]; } || \
	   { [ "$(EMBEDSERV_MODE)" = "custom" ] && [ "$$embeddings_url" = "$(EMBEDSERV_DEFAULT_UPSTREAM_URL)" ]; }; then \
		echo "ERROR: embedding lifecycle mode disagrees with the canonical upstream URL"; \
		exit 1; \
	fi; \
	if [ "$(EMBEDSERV_MODE)" = "custom" ]; then \
		"$${MAKE:-make}" --no-print-directory embedserv-stop-if-started; \
	fi

# ══════════════════════════════════════════════════════════════════════════════
# VPN signup, order status, balance, and connection information
# ══════════════════════════════════════════════════════════════════════════════

# Start standalone Myst container for initial signup/payment, then run the
# signup helper which creates an identity, registers it, and creates a
# payment order. The payment URL is printed to stdout.
# Setup mode starts only the local daemon; the host helper owns each explicit
# identity, registration, and payment operation.
vpn-signup-orderform: myst-image-ready
	@set -eu; \
	python3 myst/signup_guard.py --container-bin "$(CONTAINER_BIN)" --container-name "$(MYST_CONTAINER_NAME)" --allowed-project "$(MYST_SIGNUP_PROJECT)"; \
	$(MAKE) --no-print-directory claim-shared-data-engine; \
	echo "Starting standalone Myst signup container..."; \
	mkdir -p $(MYST_DATA_DIR); \
	MYST_SETUP_ONLY=true MYST_AUTO_CONNECT=false MYST_RESTART_POLICY=no \
		COMPOSE_FILE=$(MYST_COMPOSE_FILE) "$(CONTAINER_BIN)" compose -p $(MYST_SIGNUP_PROJECT) $(COMPOSE_ENV_FILES) up -d
	@CONTAINER_BIN="$(CONTAINER_BIN)" CONTAINER_NAME="$(MYST_CONTAINER_NAME)" \
		MYST_VPN_IDENTITY="$(MYST_VPN_IDENTITY)" \
		MYST_VPN_ORDER_AMOUNT="$(MYST_VPN_ORDER_AMOUNT)" \
		MYST_VPN_ORDER_CURRENCY="$(MYST_VPN_ORDER_CURRENCY)" \
		MYST_VPN_ORDER_GATEWAY="$(MYST_VPN_ORDER_GATEWAY)" \
		MYST_VPN_ORDER_COUNTRY="$(MYST_VPN_ORDER_COUNTRY)" \
		MYST_VPN_ORDER_GATEWAY_DATA="$(MYST_VPN_ORDER_GATEWAY_DATA)" \
		$(MYST_VPN_CLI) signup

# Start standalone Myst container for initial signup, then run the blockchain
# helper which creates an identity, registers it, and prints the consumer
# channel address for direct on-chain $MYST transfer (Polygon). No payment
# order is created.
# Setup mode starts only the local daemon; the host helper owns each explicit
# identity and registration operation. This target never creates an order.
vpn-signup-blockchain: myst-image-ready
	@set -eu; \
	python3 myst/signup_guard.py --container-bin "$(CONTAINER_BIN)" --container-name "$(MYST_CONTAINER_NAME)" --allowed-project "$(MYST_SIGNUP_PROJECT)"; \
	$(MAKE) --no-print-directory claim-shared-data-engine; \
	echo "Starting standalone Myst signup container..."; \
	mkdir -p $(MYST_DATA_DIR); \
	MYST_SETUP_ONLY=true MYST_AUTO_CONNECT=false MYST_RESTART_POLICY=no \
		COMPOSE_FILE=$(MYST_COMPOSE_FILE) "$(CONTAINER_BIN)" compose -p $(MYST_SIGNUP_PROJECT) $(COMPOSE_ENV_FILES) up -d
	@CONTAINER_BIN="$(CONTAINER_BIN)" CONTAINER_NAME="$(MYST_CONTAINER_NAME)" \
		MYST_VPN_IDENTITY="$(MYST_VPN_IDENTITY)" \
		$(MYST_VPN_CLI) blockchain

vpn-signup-stop:
	@python3 myst/signup_guard.py --container-bin "$(CONTAINER_BIN)" --container-name "$(MYST_CONTAINER_NAME)" --allowed-project "$(MYST_SIGNUP_PROJECT)" --require-existing
	@COMPOSE_FILE=$(MYST_COMPOSE_FILE) "$(CONTAINER_BIN)" compose -p $(MYST_SIGNUP_PROJECT) $(COMPOSE_ENV_FILES) down --remove-orphans
	@$(MAKE) --no-print-directory release-shared-data-engine

# Show identity, balance, registration status, all orders, and payment URLs
# for any unpaid orders. Works against whichever myst container is running.
vpn-orderstatus:
	@CONTAINER_BIN="$(CONTAINER_BIN)" CONTAINER_NAME="$(MYST_CONTAINER_NAME)" \
		MYST_VPN_IDENTITY="$(MYST_VPN_IDENTITY)" \
		$(MYST_VPN_CLI) orderstatus

# Quick balance check. Works against whichever myst container is running.
vpn-balance:
	@CONTAINER_BIN="$(CONTAINER_BIN)" CONTAINER_NAME="$(MYST_CONTAINER_NAME)" \
		MYST_VPN_IDENTITY="$(MYST_VPN_IDENTITY)" \
		$(MYST_VPN_CLI) balance

# Show the active connection, including the selected provider identity. Works
# against either the integrated or standalone container when it is running.
vpn-connection-info:
	@"$(CONTAINER_BIN)" exec "$(MYST_CONTAINER_NAME)" myst connection info

# Prerequisite for up-lite/up-full: stop signup container if running and
# verify that a Myst identity (keystore) exists. If no keystore is found,
# instruct the user to run 'make vpn-signup-orderform' or 'make vpn-signup-blockchain' first.
ensure-myst-funded:
	@set -eu; \
	myst_kind="$$(python3 myst/signup_guard.py --container-bin "$(CONTAINER_BIN)" --container-name "$(MYST_CONTAINER_NAME)" --allowed-project "$(MYST_SIGNUP_PROJECT)" --classify)"; \
	case "$$myst_kind" in \
		integrated) echo "Integrated Onyx Myst container exists; preserving its routing namespace." ;; \
		setup) \
			echo "Stopping standalone Myst signup container (wallet data is preserved)..."; \
			COMPOSE_FILE=$(MYST_COMPOSE_FILE) "$(CONTAINER_BIN)" compose -p $(MYST_SIGNUP_PROJECT) $(COMPOSE_ENV_FILES) down --remove-orphans \
			;; \
		absent) ;; \
		*) echo "ERROR: Unexpected Myst container classification: $$myst_kind" >&2; exit 1 ;; \
	esac; \
	if [ "$(MYST_VPN_ENABLED)" = "false" ]; then \
		echo "MYST_VPN_ENABLED=false — skipping Myst keystore/funding check."; \
		exit 0; \
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
