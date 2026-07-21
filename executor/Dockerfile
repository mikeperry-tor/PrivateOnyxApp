ARG PYTHON_EXECUTOR_UPSTREAM_IMAGE=onyxdotapp/python-executor-sci:0.4.4@sha256:a8a3ef036c732917143d3aea4a1a4c6ea0ce3664d5563f52d8163c16f782c947

FROM ${PYTHON_EXECUTOR_UPSTREAM_IMAGE}

COPY executor/requirements.txt /tmp/private-onyx-executor-requirements.txt
RUN uv pip install \
        --python /opt/executor-venv/bin/python \
        --require-hashes \
        --no-cache \
        -r /tmp/private-onyx-executor-requirements.txt \
    && python -c "import sympy; assert sympy.__version__ == '1.14.0'" \
    && rm -f /tmp/private-onyx-executor-requirements.txt
