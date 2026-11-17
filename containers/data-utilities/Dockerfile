FROM ghcr.io/astral-sh/uv:0.13.0 AS uv
FROM python:3.13-slim
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends graphviz fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/aiscientist-mcp
COPY pyproject.toml uv.lock ./
COPY packages ./packages
RUN uv sync --frozen --no-dev --package aiscientist-mcp-data-utilities --no-editable
ENV PATH="/opt/aiscientist-mcp/.venv/bin:$PATH"
CMD ["data-utilities-mcp"]
