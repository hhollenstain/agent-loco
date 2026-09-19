# Multi-arch agent image (linux/amd64 + linux/arm64).
# The agent itself is CPU-bound. GPUs belong to the model server, not this image.
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates bash \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/agent-loco

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 loco \
    && mkdir -p /workspaces \
    && chown -R loco:loco /opt/agent-loco /workspaces

USER loco
WORKDIR /workspaces

ENTRYPOINT ["loco"]
CMD ["doctor"]
