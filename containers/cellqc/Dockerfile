# Build from the repository root for linux/amd64. Both environments use exact archive hashes.
FROM mambaorg/micromamba:2.10.0-debian12-slim@sha256:9240d062c31af45efe9d3e5dfd68d6f27eac8ef136ae5d9ce5943958a2291923
LABEL org.opencontainers.image.source="https://github.com/lijinbio/aiscientist-mcp" \
      org.opencontainers.image.description="MCP server for the frozen CellQC 0.3.6 scientific environment" \
      org.opencontainers.image.licenses="MIT"
USER root
RUN mkdir -p /opt/mcp-env && chown "$MAMBA_USER:$MAMBA_USER" /opt/mcp-env
USER $MAMBA_USER
COPY --chown=$MAMBA_USER:$MAMBA_USER containers/cellqc/*-linux-64.lock /opt/aiscientist-locks/
# Preserve all 508 original scientific packages, including Python/R builds and native libraries.
# Install the MCP transport in its own interpreter so it cannot update the scientific stack.
RUN micromamba install -y -n base --file /opt/aiscientist-locks/science-linux-64.lock \
    && micromamba create -y -p /opt/mcp-env --file /opt/aiscientist-locks/mcp-linux-64.lock \
    && micromamba clean -a -y
COPY --chown=$MAMBA_USER:$MAMBA_USER packages/common /opt/aiscientist-mcp/packages/common
COPY --chown=$MAMBA_USER:$MAMBA_USER packages/cellqc /opt/aiscientist-mcp/packages/cellqc
COPY --chown=$MAMBA_USER:$MAMBA_USER containers/cellqc/verify_environment.py /opt/aiscientist-locks/
COPY --chown=$MAMBA_USER:$MAMBA_USER --chmod=755 containers/cellqc/worker-python /opt/aiscientist-mcp/worker-python
RUN /opt/mcp-env/bin/python -m pip wheel --no-deps --no-build-isolation --wheel-dir /tmp/mcp-wheels \
        /opt/aiscientist-mcp/packages/common /opt/aiscientist-mcp/packages/cellqc \
    && /opt/mcp-env/bin/python -m pip install --no-deps /tmp/mcp-wheels/*.whl \
    && /opt/conda/bin/python -m pip install --no-deps /tmp/mcp-wheels/*.whl \
    && /opt/conda/bin/python /opt/aiscientist-locks/verify_environment.py \
        /opt/conda /opt/aiscientist-locks/science-linux-64.lock \
    && /opt/mcp-env/bin/python /opt/aiscientist-locks/verify_environment.py \
        /opt/mcp-env /opt/aiscientist-locks/mcp-linux-64.lock \
    && /opt/aiscientist-mcp/worker-python -c "from aiscientist_mcp.cellqc import core; from aiscientist_mcp.cellqc.settings import load_defaults; load_defaults()" \
    && /opt/conda/bin/cellqc --help > /dev/null \
    && /opt/mcp-env/bin/python -c "from aiscientist_mcp.cellqc.server import server" \
    && rm -rf /tmp/mcp-wheels
# The inherited entrypoint must not reactivate base and shadow the MCP CLI.
# Scientific subprocesses select base explicitly through worker-python.
ENV PATH=/opt/mcp-env/bin:/opt/conda/bin:$PATH \
    MAMBA_SKIP_ACTIVATE=1 \
    AISCI_JOB_PYTHON=/opt/aiscientist-mcp/worker-python
CMD ["/opt/mcp-env/bin/cellqc-mcp"]
