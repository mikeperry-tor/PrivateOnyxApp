ARG OBSCURA_BUILDER_IMAGE=docker.io/library/rust:1.88.0-bookworm@sha256:af306cfa71d987911a781c37b59d7d67d934f49684058f96cf72079c3626bfe0
ARG OBSCURA_UPSTREAM_IMAGE=docker.io/h4ckf0r0day/obscura:0.2.0@sha256:78c99ac89d010d444d96d85c183a2db912c41f807b7807d697df98ab7e4bd3c2

FROM ${OBSCURA_BUILDER_IMAGE} AS builder

ARG OBSCURA_SOURCE_REF
ARG OBSCURA_SOURCE_SHA256
ARG OBSCURA_RELEASE_VERSION

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        clang \
        cmake \
        git \
        libclang-dev \
        make \
        perl \
        pkg-config \
        python3 \
    && rm -rf /var/lib/apt/lists/*

COPY browser/obscura_image/fetch_source.py /wrapper/fetch_source.py
COPY browser/obscura_image/patches/ /wrapper/patches/

RUN set -eu; \
    python3 /wrapper/fetch_source.py \
        --ref "${OBSCURA_SOURCE_REF}" \
        --sha256 "${OBSCURA_SOURCE_SHA256}" \
        --output-dir /build \
    && test "$(cat /wrapper/patches/series)" = "$(printf '%s\n%s\n%s' \
        0001-stealth-native-post.patch \
        0002-target-fingerprint-seed.patch \
        0003-search-runtime-compatibility.patch)" \
    && test "$(find /wrapper/patches -maxdepth 1 -type f -name '*.patch' \
        -exec basename {} \; | LC_ALL=C sort)" = "$(printf '%s\n%s\n%s' \
        0001-stealth-native-post.patch \
        0002-target-fingerprint-seed.patch \
        0003-search-runtime-compatibility.patch)" \
    && while IFS= read -r patch; do \
        git -C /build apply --check "/wrapper/patches/${patch}"; \
        git -C /build apply "/wrapper/patches/${patch}"; \
    done < /wrapper/patches/series

WORKDIR /build

RUN cargo test --locked --features stealth \
        -p obscura-net private_onyx_stealth_

RUN cargo test --locked --features stealth \
        -p obscura-js private_onyx_

RUN OBSCURA_VERSION="${OBSCURA_RELEASE_VERSION}-private-onyx-search-v1" \
        cargo build --release --locked --features stealth \
        -p obscura-cli --bin obscura --bin obscura-worker

FROM ${OBSCURA_UPSTREAM_IMAGE}

COPY --from=builder /build/target/release/obscura /obscura
COPY --from=builder /build/target/release/obscura-worker /obscura-worker
