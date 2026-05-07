# Private Onyx.App Compose Set

Docker Compose wrapper for running [Onyx](https://github.com/onyx-dot-app/onyx) with the teep private inference proxy, with all traffic sent over a [Mysterium](https://github.com/mysteriumnetwork/node) VPN connection.

## What This Stack Does

- Runs Onyx so it can use teep as a private inference proxy.
- Sends Onyx web traffic, teep traffic, and SearxNG search traffic through the Mysterium VPN container.
- Bootstraps a new Mysterium wallet identity automatically on first startup if none exists.
- Supports funding that wallet with cryptocurrency so the VPN consumer connection can be used.
- Exposes host-facing endpoints for local use:
  - Onyx web UI (`3000`)
  - SearxNG (`8080` by default)
  - teep (`8337`)

## Repository Layout

- `docker-compose.yaml`: wrapper compose stack
- `myst/docker-compose.yaml`: Myst consumer container configuration
- `myst/build/Dockerfile`: Myst image build (repo checkout inside Docker build)
- `teep/build/Dockerfile`: teep image build (repo checkout inside Docker build)
- `.env.wrapper`: runtime env and host port settings
- `Makefile`: main operational entrypoints

## Prerequisites

- Docker Desktop (or Docker Engine + Compose v2)
- `make`
- Internet access for image builds and provider APIs

## Configure Environment

Edit `.env.wrapper` as needed:

- Host ports:
  - `HOST_PORT` (Onyx UI bridge)
  - `SEARXNG_PORT`
  - `TEEP_PORT`
- Provider/API config:
  - Set at least one teep key (for example `NEARAI_API_KEY`, `VENICE_API_KEY`, or `CHUTES_API_KEY`)
- Optional Myst provider pinning:
  - `MYST_PROVIDER_IDS`

## Build Images

Build custom Myst image:

```bash
make myst-build
```

Build teep image:

```bash
make teep-build
```

Override image names if desired:

```bash
make myst-build MYST_IMAGE=local/myst:docker_host_fixes_with_logs
make teep-build TEEP_IMAGE=local/teep:main
```

## Start and Stop

Lite mode (wrapper + Onyx lite):

```bash
make up-lite
```

Full mode (wrapper + Onyx full):

```bash
make up-full
```

Stop:

```bash
make down-lite
# or
make down-full
```

Status/logs:

```bash
make ps-lite
make logs-lite
```

## Service Endpoints

- Onyx UI: `http://localhost:3000`
- SearxNG: `http://localhost:8080`
- teep proxy: `http://localhost:8337`
- teep health: `http://localhost:8337/health`
- teep models: `http://localhost:8337/v1/models`

## Notes

- `up-lite` and `up-full` automatically ensure required Myst and teep images exist before compose startup.
- `teep` runs `teep serve` in the Myst shared network namespace and uses a container healthcheck on `/health`.
- On first run, Myst can create a wallet identity in `docker-data/myst-data` and keep it for reuse across restarts.
- Fund the Myst wallet identity with crypto to enable and sustain VPN usage as a consumer.
- Persistent data is under `docker-data/`.
