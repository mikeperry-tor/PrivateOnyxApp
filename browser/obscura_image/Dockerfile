ARG OBSCURA_BUILDER_IMAGE=docker.io/library/rust:1.94.0-bookworm@sha256:365468470075493dc4583f47387001854321c5a8583ea9604b297e67f01c5a4f
ARG OBSCURA_UPSTREAM_IMAGE=docker.io/h4ckf0r0day/obscura:0.2.6@sha256:76522dd288b0c0186b360fd6e6133ef55908e1fc3b9c4b7ad6240bf8682d998c

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
    && test "$(cat /wrapper/patches/series)" = "$(printf '%s\n%s\n%s\n%s' \
        0001-stealth-native-post.patch \
        0002-target-fingerprint-seed.patch \
        0003-search-runtime-compatibility.patch \
        0004-connection-storage-isolation.patch)" \
    && test "$(find /wrapper/patches -maxdepth 1 -type f -name '*.patch' \
        -exec basename {} \; | LC_ALL=C sort)" = "$(printf '%s\n%s\n%s\n%s' \
        0001-stealth-native-post.patch \
        0002-target-fingerprint-seed.patch \
        0003-search-runtime-compatibility.patch \
        0004-connection-storage-isolation.patch)" \
    && while IFS= read -r patch; do \
        git -C /build apply --check "/wrapper/patches/${patch}"; \
        git -C /build apply "/wrapper/patches/${patch}"; \
    done < /wrapper/patches/series

WORKDIR /build

# Cargo exits successfully when a test filter matches no tests. Enumerate the
# selected tests first so a renamed or removed upstream contract fails the
# build instead of silently disabling its gate.
RUN set -eu; \
    run_gate() { \
        mode="$1"; package="$2"; shift 2; \
        if [ "$mode" = stealth ]; then \
            cargo test --locked --no-default-features --features stealth \
                -p "$package" "$@" -- --list | grep -q ': test$'; \
            cargo test --locked --no-default-features --features stealth \
                -p "$package" "$@"; \
        else \
            cargo test --locked --no-default-features \
                -p "$package" "$@" -- --list | grep -q ': test$'; \
            cargo test --locked --no-default-features \
                -p "$package" "$@"; \
        fi; \
    }; \
    run_gate stealth obscura-net private_onyx_stealth_; \
    run_gate stealth obscura-net stealth_resolver_; \
    run_gate stealth obscura-net cookies::tests; \
    run_gate plain obscura-cdp clear_cookies_; \
    run_gate plain obscura-cdp cookie_params::tests; \
    run_gate stealth obscura-js private_onyx_; \
    for check in \
        classic_code_cache_reuses_compilation_not_page_state \
        indexed_db::tests indexeddb_ origin_storage_ web_storage_ \
        watchdog fetch_abort \
        fetch_resolves_headers_before_the_response_body_finishes \
        repeated_fetch_clone_cancellation cancelling_all_fetch_clones; do \
        run_gate stealth obscura-js "$check"; \
    done; \
    run_gate stealth obscura-browser private_onyx_; \
    run_gate stealth obscura-browser \
        frame_location_navigation_loads_the_child_and_ignores_detached_windows; \
    run_gate stealth obscura-browser \
        navigating_a_frame_from_the_page_keeps_the_top_document

RUN OBSCURA_VERSION="${OBSCURA_RELEASE_VERSION}-private-onyx-search-v1" \
        cargo build --release --locked --no-default-features --features stealth \
        -p obscura-cli --bin obscura --bin obscura-worker

FROM ${OBSCURA_UPSTREAM_IMAGE}

COPY --from=builder /build/target/release/obscura /obscura
COPY --from=builder /build/target/release/obscura-worker /obscura-worker
