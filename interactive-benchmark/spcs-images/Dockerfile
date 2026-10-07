ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ARG SOURCE_URL=https://github.com/Snowflake-Labs/interactive-analytics
ARG SOURCE_REVISION=local
ARG OWNER_EMAIL=local-build
ARG OWNER_SLACK=local-build
ARG OWNER_TEAM=local-build
ARG JIRA_AREA=local-build
ARG JIRA_COMPONENT=local-build
ARG UV_VERSION=0.12.17

LABEL org.opencontainers.image.source="${SOURCE_URL}" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      com.snowflake.owners.email="${OWNER_EMAIL}" \
      com.snowflake.owners.slack="${OWNER_SLACK}" \
      com.snowflake.owners.team="${OWNER_TEAM}" \
      com.snowflake.owners.jira_area="${JIRA_AREA}" \
      com.snowflake.owners.jira_component="${JIRA_COMPONENT}"

USER 0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH=/root/.local/bin:$PATH

RUN if ! command -v uv >/dev/null 2>&1; then \
      if command -v apt-get >/dev/null 2>&1; then \
        apt-get update \
        && apt-get install -y --no-install-recommends curl ca-certificates \
        && curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | sh \
        && apt-get purge -y --auto-remove curl \
        && rm -rf /var/lib/apt/lists/*; \
      elif command -v apk >/dev/null 2>&1; then \
        apk add --no-cache --virtual .uv-install curl ca-certificates \
        && curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | sh \
        && apk del .uv-install; \
      else \
        echo "Base image must provide uv, apt-get, or apk" >&2; \
        exit 1; \
      fi; \
    fi

WORKDIR /app

COPY api/pyproject.toml api/uv.lock /app/api/
RUN cd /app/api \
    && UV_PROJECT_ENVIRONMENT=/opt/venvs/api uv sync --frozen --no-dev --no-install-project

COPY locust/pyproject.toml locust/uv.lock /app/locust/
RUN cd /app/locust \
    && UV_PROJECT_ENVIRONMENT=/opt/venvs/locust uv sync --frozen --no-dev --no-install-project

COPY controller/pyproject.toml controller/uv.lock /app/controller/
RUN cd /app/controller \
    && UV_PROJECT_ENVIRONMENT=/opt/venvs/controller uv sync --frozen --no-dev --no-install-project

COPY api/server.py api/entrypoint.sh /app/api/
COPY locust/locustfile.py locust/entrypoint.sh /app/locust/
COPY controller/iwb /app/controller/iwb
COPY role-entrypoint.sh /usr/local/bin/benchmark-entrypoint

RUN chmod +x \
      /app/api/entrypoint.sh \
      /app/locust/entrypoint.sh \
      /usr/local/bin/benchmark-entrypoint

# UID 65532 has no passwd entry in some bases, which leaves HOME=/ (read-only).
ENV HOME=/tmp
USER 65532:65532

EXPOSE 3000 8089

ENTRYPOINT ["/usr/local/bin/benchmark-entrypoint"]
