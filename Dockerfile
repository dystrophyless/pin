FROM ghcr.io/astral-sh/uv:0.10.4 AS uv

FROM python:3.12.15-slim-trixie AS build
COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/pin/.venv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --extra speech --no-editable \
    && for entrypoint in /opt/pin/.venv/bin/pin-* /opt/pin/.venv/bin/alembic; do \
        test -x "$entrypoint" || exit 1; \
        test "$(head -n 1 "$entrypoint")" = '#!/opt/pin/.venv/bin/python' || exit 1; \
    done

FROM python:3.12.15-slim-trixie
ENV PATH=/opt/pin/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 pin \
    && useradd --system --uid 10001 --gid pin --home-dir /opt/pin --shell /usr/sbin/nologin pin \
    && mkdir -p /var/lib/pin-retention /var/cache/huggingface /var/lib/pin/speech-tmp \
    && chown -R 10001:10001 /var/lib/pin-retention /var/cache/huggingface /var/lib/pin
WORKDIR /opt/pin
COPY --from=build --chown=10001:10001 /opt/pin/.venv /opt/pin/.venv
COPY --chown=root:root alembic.ini ./alembic.ini
COPY --chown=root:root migrations ./migrations
USER 10001:10001
RUN pin-gateway --help >/dev/null && alembic --help >/dev/null
CMD ["pin-gateway"]
