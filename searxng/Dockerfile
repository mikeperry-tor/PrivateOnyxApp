ARG SEARXNG_UPSTREAM_IMAGE=docker.io/searxng/searxng:2026.7.15-7b2199ecd

FROM docker.io/library/python:3.14-slim-bookworm AS python-runtime-deps
COPY searxng/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --no-deps --require-hashes \
    --target /opt/runtime-deps -r /tmp/requirements.txt

FROM ${SEARXNG_UPSTREAM_IMAGE}

COPY --from=python-runtime-deps /opt/runtime-deps /usr/local/searxng/.venv/lib/python3.14/site-packages
RUN --mount=from=python-runtime-deps,source=/usr/local/lib/python3.14/site-packages/pip,target=/tmp/pip \
    PYTHONPATH=/tmp /usr/local/searxng/.venv/bin/python -m pip check
COPY browser/obscura_client/private_onyx_obscura /usr/local/lib/private_onyx_obscura
COPY searxng/engines/_obscura.py /usr/local/searxng/searx/engines/_obscura.py
COPY searxng/engines/google2.py /usr/local/searxng/searx/engines/google2.py
COPY searxng/engines/brave2.py /usr/local/searxng/searx/engines/brave2.py
COPY searxng/engines/duckduckgo2.py /usr/local/searxng/searx/engines/duckduckgo2.py
COPY searxng/engines/startpage2.py /usr/local/searxng/searx/engines/startpage2.py
COPY searxng/engines/bing2.py /usr/local/searxng/searx/engines/bing2.py
COPY searxng/engines/_x402_payment.py searxng/engines/_x402_admission.py searxng/engines/x402exa.py /usr/local/searxng/searx/engines/
RUN PYTHONPATH=/usr/local/lib /usr/local/searxng/.venv/bin/python -c "import websockets, httpx, private_onyx_obscura; from eth_account import Account; from x402 import x402ClientSync; from x402.mechanisms.evm.signers import EthAccountSigner; from x402.mechanisms.evm.exact.client import ExactEvmScheme; Account.create()"
