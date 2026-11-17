# Original scientific base, verified with docker buildx imagetools inspect on 2026-11-12.
# The trained model and validated harness are operator-provided read-only mounts.
ARG SCGPT_BASE_IMAGE=pytorch/pytorch:2.3.0-cuda12.1-cudnn8-runtime@sha256:0279f7aa29974bf64e61d0ff6e979b41a249b3662a46e30778dbf80b8c99c361
FROM ghcr.io/astral-sh/uv:0.13.0 AS uv
FROM ${SCGPT_BASE_IMAGE}
COPY --from=uv /uv /uvx /usr/local/bin/
LABEL org.opencontainers.image.source="https://github.com/lijinbio/aiscientist-mcp" \
      org.opencontainers.image.description="MCP scGPT reference annotation using the validated GPU environment" \
      org.opencontainers.image.licenses="MIT"
WORKDIR /opt/aiscientist-mcp
COPY containers/scgpt-annotation/scientific-constraints.txt containers/scgpt-annotation/scientific-requirements.txt /opt/scgpt/
RUN python -m pip install --no-cache-dir -c /opt/scgpt/scientific-constraints.txt -r /opt/scgpt/scientific-requirements.txt \
    && python -c "import torch, scgpt, scanpy; assert torch.__version__ == '2.3.0'; assert scgpt.__version__ == '0.2.4'; assert scanpy.__version__ == '1.11.5'"
# Preserve the scientific Python before creating the MCP runtime. The original validated
# environment can remain Python 3.10; only the MCP protocol server requires Python >=3.11.
RUN python -c "import sys; open('/opt/scgpt-scientific-python', 'w').write(sys.executable)"
COPY pyproject.toml uv.lock ./
COPY packages/ ./packages/
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
RUN uv python install 3.13.16 \
    && uv venv --python 3.13.16 /opt/aiscientist-mcp/.venv \
    && uv export --frozen --no-dev --package aiscientist-mcp-scgpt-annotation --no-emit-workspace -o /tmp/mcp-requirements.txt \
    && uv pip install --python /opt/aiscientist-mcp/.venv/bin/python -r /tmp/mcp-requirements.txt \
    && uv pip install --python /opt/aiscientist-mcp/.venv/bin/python --no-deps ./packages/common ./packages/scgpt-annotation \
    && chmod -R a+rX /opt/uv-python /opt/aiscientist-mcp/.venv
COPY containers/scgpt-annotation/entrypoint.sh /opt/scgpt-mcp-entrypoint
RUN chmod 755 /opt/scgpt-mcp-entrypoint
ENV PATH="/opt/aiscientist-mcp/.venv/bin:$PATH" \
    SCGPT_REFACTOR=/opt/scgpt/scGPT_refactor \
    WANDB_MODE=offline \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    NUMBA_CACHE_DIR=/tmp/numba \
    MPLBACKEND=Agg
ENTRYPOINT ["/opt/scgpt-mcp-entrypoint"]
CMD ["scgpt-annotation-mcp"]
