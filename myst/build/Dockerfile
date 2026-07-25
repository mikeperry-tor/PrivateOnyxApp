FROM --platform=$BUILDPLATFORM docker.io/library/golang:1.26-alpine AS builder

# Install packages
RUN apk add --no-cache git bash gcc musl-dev make linux-headers

ARG MYST_NODE_REPO=https://github.com/mikeperry-tor/node.git
ARG MYST_NODE_REF

ARG BUILD_BRANCH=""
ARG BUILD_COMMIT=""
ARG BUILD_NUMBER=""
ARG BUILD_VERSION=""

# Compile application
WORKDIR /go/src/github.com/mysteriumnetwork/node
RUN test -n "$MYST_NODE_REF" \
    && git clone "$MYST_NODE_REPO" . \
    && git checkout --detach "$MYST_NODE_REF" \
    && test "$(git rev-parse HEAD)" = "$MYST_NODE_REF"
RUN go mod download
ARG TARGETOS TARGETARCH
RUN GOOS=$TARGETOS GOARCH=$TARGETARCH BUILD_STATIC=1 bin/build


FROM docker.io/library/alpine:3.22

ARG MYST_NODE_REPO=https://github.com/mikeperry-tor/node.git
ARG MYST_NODE_REF

LABEL org.opencontainers.image.source=$MYST_NODE_REPO \
      org.opencontainers.image.revision=$MYST_NODE_REF

# Install packages
RUN apk add --no-cache iptables ipset ca-certificates openvpn bash sudo openresolv bind-tools

COPY --from=builder /go/src/github.com/mysteriumnetwork/node/bin/helpers/prepare-run-env.sh /usr/local/bin/prepare-run-env.sh
COPY --from=builder /go/src/github.com/mysteriumnetwork/node/bin/docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

COPY --from=builder /go/src/github.com/mysteriumnetwork/node/bin/package/config/common /etc/mysterium-node
COPY --from=builder /go/src/github.com/mysteriumnetwork/node/bin/package/config/linux /etc/mysterium-node

COPY --from=builder /go/src/github.com/mysteriumnetwork/node/build/myst/myst /usr/bin/myst

WORKDIR /var/run/myst
