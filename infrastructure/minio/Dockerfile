# Build the pinned upstream releases: their prebuilt images are no longer available.
FROM golang:1.23.6-bookworm@sha256:462f68e1109cc0415f58ba591f11e650b38e193fddc4a683a3b77d29be8bfb2c AS builder

ARG MINIO_COMPONENT=minio
ARG MINIO_RELEASE=RELEASE.2025-02-07T23-21-09Z
ARG MINIO_COMMIT=703f51164d3d0c44af41b0d86075a1f61e4779e7

WORKDIR /source
RUN git clone --depth 1 --branch "${MINIO_RELEASE}" \
      "https://github.com/minio/${MINIO_COMPONENT}.git" . \
    && test "$(git rev-parse HEAD)" = "${MINIO_COMMIT}"
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 go build -p 2 -trimpath -tags kqueue \
      -ldflags="$(MINIO_RELEASE=RELEASE MC_RELEASE=RELEASE go run buildscripts/gen-ldflags.go)" \
      -o /out/object-store .

FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587

ARG MINIO_COMPONENT=minio
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /out/object-store /usr/local/bin/object-store
COPY --from=builder /source/LICENSE /usr/share/doc/object-store/LICENSE
RUN ln -s /usr/local/bin/object-store "/usr/local/bin/${MINIO_COMPONENT}"

ENTRYPOINT ["/usr/local/bin/object-store"]
