# Build from the workspace root for linux/amd64. Scientific wheels match the
# deployed analysis.sif + analysis-pydeps RECORDs; see science-provenance.json.
FROM ghcr.io/astral-sh/uv:0.13.0 AS uv
# Official Python image index inspected 2026-11-13; exact scientific Python 3.11.15.
FROM python:3.11.15-slim@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff
COPY --from=uv /uv /uvx /usr/local/bin/
LABEL org.opencontainers.image.source="https://github.com/lijinbio/aiscientist-mcp" \
      org.opencontainers.image.description="Single-cell RNA MCP server with frozen scientific dependencies" \
      org.opencontainers.image.licenses="MIT"
WORKDIR /opt/aiscientist-mcp
COPY containers/scrna-analysis/scientific-requirements.lock containers/scrna-analysis/science-provenance.json containers/scrna-analysis/verify_environment.py /opt/scrna-locks/
RUN /usr/local/bin/python -m pip install --no-cache-dir --require-hashes -r /opt/scrna-locks/scientific-requirements.lock \
    && /usr/local/bin/python /opt/scrna-locks/verify_environment.py
# The MCP dependency closure lives in a separate interpreter. Installing or
# updating its SDK cannot change pandas, Scanpy, NumPy or the scientific worker.
COPY pyproject.toml uv.lock ./
COPY packages/ ./packages/
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
RUN uv python install 3.13.16 \
    && uv venv --python 3.13.16 /opt/aiscientist-mcp/.venv \
    && uv export --frozen --no-dev --package aiscientist-mcp-common --no-emit-workspace -o /tmp/mcp-requirements.txt > /dev/null \
    && uv pip install --python /opt/aiscientist-mcp/.venv/bin/python --require-hashes -r /tmp/mcp-requirements.txt \
    && uv build --wheel --package aiscientist-mcp-common --out-dir /tmp/mcp-wheels \
    && uv build --wheel --package aiscientist-mcp-scrna-analysis --out-dir /tmp/mcp-wheels \
    && uv pip install --python /opt/aiscientist-mcp/.venv/bin/python --no-deps /tmp/mcp-wheels/*.whl \
    && uv pip install --python /usr/local/bin/python --no-deps /tmp/mcp-wheels/*.whl \
    && chmod -R a+rX /opt/uv-python /opt/aiscientist-mcp/.venv \
    && rm -rf /tmp/mcp-wheels
COPY --chmod=755 containers/scrna-analysis/worker-python /opt/aiscientist-mcp/worker-python
ENV PATH="/opt/aiscientist-mcp/.venv/bin:$PATH" \
    AISCI_JOB_PYTHON=/opt/aiscientist-mcp/worker-python \
    MPLBACKEND=Agg
RUN /opt/aiscientist-mcp/worker-python /opt/scrna-locks/verify_environment.py \
    && /opt/aiscientist-mcp/worker-python -c "import scanpy, gseapy, pydeseq2, harmonypy, skimage; from aiscientist_mcp.scrna_analysis import worker" \
    && python -c "from aiscientist_mcp.scrna_analysis.server import server"
CMD ["scrna-analysis-mcp"]
