ARG PYTHON_SLIM_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_SLIM_IMAGE}

COPY crw/cdp-shim-requirements.txt /tmp/cdp-shim-requirements.txt
RUN pip install \
      --no-cache-dir \
      --require-hashes \
      --requirement /tmp/cdp-shim-requirements.txt \
    && rm /tmp/cdp-shim-requirements.txt

WORKDIR /app
CMD ["python", "-u", "/app/cdp_shim.py"]
