ARG PYTHON_EXECUTOR_UPSTREAM_IMAGE=docker.io/onyxdotapp/python-executor-sci:0.4.5@sha256:9fad684ba9588ca37312a3a561da0566394d89051677294d0b262d1470797bff

FROM ${PYTHON_EXECUTOR_UPSTREAM_IMAGE}

ARG OS_SECURITY_UPDATE_REVISION
COPY onyx/code-interpreter/upgrade-os.sh /tmp/private-onyx-upgrade-os.sh
RUN test -n "${OS_SECURITY_UPDATE_REVISION}" \
    && sh /tmp/private-onyx-upgrade-os.sh \
    && rm /tmp/private-onyx-upgrade-os.sh
LABEL org.private-onyx.os-security-update-revision=${OS_SECURITY_UPDATE_REVISION}

COPY executor/requirements.txt /tmp/private-onyx-executor-requirements.txt
RUN uv pip install \
        --python /opt/executor-venv/bin/python \
        --require-hashes \
        --no-cache \
        -r /tmp/private-onyx-executor-requirements.txt \
    && python -c "import sympy; assert sympy.__version__ == '1.14.0'" \
    && uv pip check --python /opt/executor-venv/bin/python \
    && rm -f /tmp/private-onyx-executor-requirements.txt
