FROM --platform=$BUILDPLATFORM golang:1.26-alpine AS builder

RUN apk add --no-cache git ca-certificates

ARG TEEP_REPO=https://github.com/13rac1/teep.git
ARG TEEP_REF=main

WORKDIR /src
RUN git clone --branch "$TEEP_REF" --single-branch --depth 1 "$TEEP_REPO" .

ARG TARGETOS
ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH \
    go build -trimpath -ldflags "-s -w" -o /out/teep ./cmd/teep

FROM alpine:3.22

RUN apk add --no-cache ca-certificates

COPY --from=builder /out/teep /usr/local/bin/teep
COPY teep/build/entrypoint.sh /usr/local/bin/teep-entrypoint.sh
RUN chmod +x /usr/local/bin/teep-entrypoint.sh

EXPOSE 8337
ENTRYPOINT ["/usr/local/bin/teep-entrypoint.sh"]
CMD ["serve"]
