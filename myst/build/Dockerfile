FROM --platform=$BUILDPLATFORM golang:1.26-alpine AS builder

# Install packages
RUN apk add --no-cache git bash gcc musl-dev make linux-headers

ARG MYST_NODE_REPO=https://github.com/mikeperry-tor/node.git
ARG MYST_NODE_BRANCH=docker_host_fixes_with_logs

ARG BUILD_BRANCH=$BUILD_BRANCH
ARG BUILD_COMMIT=$BUILD_COMMIT
ARG BUILD_NUMBER=$BUILD_NUMBER
ARG BUILD_VERSION=$BUILD_VERSION

# Compile application
WORKDIR /go/src/github.com/mysteriumnetwork/node
RUN git clone --branch "$MYST_NODE_BRANCH" --single-branch --depth 1 "$MYST_NODE_REPO" .
RUN go mod download
ARG TARGETOS TARGETARCH
RUN GOOS=$TARGETOS GOARCH=$TARGETARCH BUILD_STATIC=1 bin/build


FROM alpine:3.22

# Install packages
RUN apk add --no-cache iptables ipset ca-certificates openvpn bash sudo openresolv

COPY --from=builder /go/src/github.com/mysteriumnetwork/node/bin/helpers/prepare-run-env.sh /usr/local/bin/prepare-run-env.sh
COPY --from=builder /go/src/github.com/mysteriumnetwork/node/bin/docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

COPY --from=builder /go/src/github.com/mysteriumnetwork/node/bin/package/config/common /etc/mysterium-node
COPY --from=builder /go/src/github.com/mysteriumnetwork/node/bin/package/config/linux /etc/mysterium-node

COPY --from=builder /go/src/github.com/mysteriumnetwork/node/build/myst/myst /usr/bin/myst

WORKDIR /var/run/myst
