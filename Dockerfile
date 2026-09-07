ARG PYTHON_VER="3.14"

######## BUILDER OF PYTHON APP
FROM python:${PYTHON_VER}-slim AS builder

WORKDIR /opt

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY pyproject.toml uv.lock ./

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/.venv

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync ${VERBOSE:-} --frozen --no-install-project --no-dev


FROM python:${PYTHON_VER}-slim AS runner

# Install system dependencies for BlueZ communication
RUN apt-get update && apt-get install -y --no-install-recommends \
    bluez \
    dbus \
    libglib2.0-0 \
    procps \
    && rm -rf /var/lib/apt/lists/*

ARG _USER=appuser
ARG _GROUP=appgroup
ARG APP_PORT

WORKDIR /app

RUN groupadd ${_GROUP} && useradd --no-log-init -r --no-create-home -g ${_GROUP} ${_USER} && \
    mkdir ./data && \
    chown -R  ${_USER}:${_GROUP} ./data


# Copy venv from previous stage "builder"
COPY --from=builder /opt/.venv /opt/.venv
COPY pyproject.toml .
COPY ./src src/
COPY --chmod=+x ./entrypoint.sh .

ENV PATH="/opt/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=./src

#USER ${_USER}

CMD ["/bin/bash", "-c", "/app/entrypoint.sh"]

