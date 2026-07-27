ARG PYTHON_ALPINE_IMAGE=docker.io/library/python:3.12-alpine
ARG OBSCURA_UPSTREAM_IMAGE=docker.io/h4ckf0r0day/obscura:0.1.11@sha256:e5fd7b8032a5fedc6e8e27f9d4f2e35327ea08b2548ab1372775974add171547

FROM ${PYTHON_ALPINE_IMAGE} AS release

ARG TARGETARCH
ARG OBSCURA_RELEASE_VERSION
ARG OBSCURA_RELEASE_AMD64_SHA256
ARG OBSCURA_RELEASE_ARM64_SHA256

COPY browser/obscura_image/fetch_release.py /fetch_release.py

RUN python3 /fetch_release.py \
    --version "${OBSCURA_RELEASE_VERSION}" \
    --architecture "${TARGETARCH}" \
    --amd64-sha256 "${OBSCURA_RELEASE_AMD64_SHA256}" \
    --arm64-sha256 "${OBSCURA_RELEASE_ARM64_SHA256}" \
    --output-dir /release

FROM ${OBSCURA_UPSTREAM_IMAGE}

COPY --from=release /release/obscura /obscura
COPY --from=release /release/obscura-worker /obscura-worker
