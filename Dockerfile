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

ARG _USER=appuser
ARG _GROUP=appgroup
ARG APP_PORT

RUN groupadd ${_GROUP} && useradd --no-log-init -r --no-create-home -g ${_GROUP} ${_USER}

WORKDIR /app

# Copy venv from previous stage "builder"
COPY --from=builder /opt/.venv /opt/.venv
COPY pyproject.toml .
COPY ./src src/
COPY --chmod=+x ./entrypoint.sh .

ENV PATH="/opt/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=./src

USER ${_USER}

CMD ["/bin/bash", "-c", "/app/entrypoint.sh"]








FROM python:3.14-slim

# Install system dependencies for BlueZ communication
RUN apt-get update && apt-get install -y --no-install-recommends \
    bluez \
    dbus \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir bleak

COPY monitor.py .

CMD ["python", "-u", "monitor.py"]