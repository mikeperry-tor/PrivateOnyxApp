ARG SEARXNG_UPSTREAM_IMAGE=docker.io/searxng/searxng:2026.6.26-f8ffbf36f
ARG ONYX_BACKEND_IMAGE=onyxdotapp/onyx-backend:v4.2.5
FROM ${ONYX_BACKEND_IMAGE} AS playwright-client

FROM python:3.14-slim-bookworm AS python-runtime-deps
COPY searxng/runtime-requirements.txt /tmp/runtime-requirements.txt
RUN python -m pip install --no-cache-dir --no-deps --require-hashes \
    --target /opt/runtime-deps -r /tmp/runtime-requirements.txt

FROM ${SEARXNG_UPSTREAM_IMAGE}

COPY --from=playwright-client /usr/local/lib/python3.13/site-packages/playwright /usr/local/searxng/.venv/lib/python3.14/site-packages/playwright
COPY --from=python-runtime-deps /opt/runtime-deps /usr/local/searxng/.venv/lib/python3.14/site-packages
COPY browser/obscura_client/private_onyx_obscura /usr/local/lib/private_onyx_obscura
COPY searxng/engines/_obscura.py /usr/local/searxng/searx/engines/_obscura.py
COPY searxng/engines/google2.py /usr/local/searxng/searx/engines/google2.py
COPY searxng/engines/brave2.py /usr/local/searxng/searx/engines/brave2.py
COPY searxng/engines/duckduckgo2.py /usr/local/searxng/searx/engines/duckduckgo2.py
COPY searxng/engines/startpage2.py /usr/local/searxng/searx/engines/startpage2.py
COPY searxng/engines/bing2.py /usr/local/searxng/searx/engines/bing2.py
RUN PYTHONPATH=/usr/local/lib /usr/local/searxng/.venv/bin/python -c "import playwright, websockets; from playwright.async_api import async_playwright; import private_onyx_obscura"
