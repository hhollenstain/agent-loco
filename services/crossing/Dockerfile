# Engineer dashboard. It installs the shared library and the directory package.
# It does not run cycles, so it does not need git, gh, or a model server.
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LBW_STORE_ROOT=/workspaces \
    LBW_HOST=0.0.0.0 \
    LBW_PORT=8080

WORKDIR /opt/lbw

COPY pyproject.toml README.md ./
COPY libs/lbw_shared ./libs/lbw_shared
COPY src ./src
COPY services/crossing ./services/crossing

RUN pip install --no-cache-dir ./libs/lbw_shared . ./services/crossing \
    && useradd --create-home --uid 10001 lbw \
    && mkdir -p /workspaces \
    && chown -R lbw:lbw /opt/lbw /workspaces

USER lbw
WORKDIR /workspaces

EXPOSE 8080
CMD ["python", "-m", "lbw_crossing"]
