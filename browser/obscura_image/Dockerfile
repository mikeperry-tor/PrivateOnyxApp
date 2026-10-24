ARG OBSCURA_BUILDER_IMAGE=docker.io/library/rust:1.94.0-bookworm@sha256:365468470075493dc4583f47387001854321c5a8583ea9604b297e67f01c5a4f
ARG OBSCURA_UPSTREAM_IMAGE=docker.io/h4ckf0r0day/obscura:0.2.5@sha256:354c3d2a0fa2dd2939debe6903aa26c35d121329245dfa42aa7d1c292a209814

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
    && test "$(cat /wrapper/patches/series)" = "$(printf '%s\n%s\n%s\n%s\n%s' \
        0001-stealth-native-post.patch \
        0002-target-fingerprint-seed.patch \
        0003-search-runtime-compatibility.patch \
        0004-explicit-navigation-realm.patch \
        0005-connection-storage-isolation.patch)" \
    && test "$(find /wrapper/patches -maxdepth 1 -type f -name '*.patch' \
        -exec basename {} \; | LC_ALL=C sort)" = "$(printf '%s\n%s\n%s\n%s\n%s' \
        0001-stealth-native-post.patch \
        0002-target-fingerprint-seed.patch \
        0003-search-runtime-compatibility.patch \
        0004-explicit-navigation-realm.patch \
        0005-connection-storage-isolation.patch)" \
    && while IFS= read -r patch; do \
        git -C /build apply --check "/wrapper/patches/${patch}"; \
        git -C /build apply "/wrapper/patches/${patch}"; \
    done < /wrapper/patches/series

WORKDIR /build

RUN cargo test --locked --no-default-features --features stealth \
        -p obscura-net private_onyx_stealth_

RUN cargo test --locked --no-default-features --features stealth \
        -p obscura-net stealth_resolver_

RUN cargo test --locked --no-default-features --features stealth \
        -p obscura-net cookies::tests

RUN cargo test --locked --no-default-features \
        -p obscura-cdp clear_cookies_

RUN cargo test --locked --no-default-features \
        -p obscura-cdp cookie_params::tests

RUN cargo test --locked --no-default-features --features stealth \
        -p obscura-js private_onyx_

# Native upgrade contracts are not selected by the wrapper's private_onyx_ filter.
RUN set -eu; for check in \
        classic_code_cache_reuses_compilation_not_page_state \
        indexed_db::tests indexeddb_ origin_storage_ web_storage_ \
        watchdog fetch_abort \
        fetch_resolves_headers_before_the_response_body_finishes \
        repeated_fetch_clone_cancellation cancelling_all_fetch_clones; do \
        cargo test --locked --no-default-features --features stealth \
            -p obscura-js "$check"; \
    done

RUN cargo test --locked --no-default-features --features stealth \
        -p obscura-browser private_onyx_

RUN OBSCURA_VERSION="${OBSCURA_RELEASE_VERSION}-private-onyx-search-v1" \
        cargo build --release --locked --no-default-features --features stealth \
        -p obscura-cli --bin obscura --bin obscura-worker

FROM ${OBSCURA_UPSTREAM_IMAGE}

COPY --from=builder /build/target/release/obscura /obscura
COPY --from=builder /build/target/release/obscura-worker /obscura-worker
