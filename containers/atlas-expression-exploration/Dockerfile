# ghcr.io/<owner>/mcp-atlas-expression-exploration:<version>
# Build from the repository root:
#   docker build -f containers/atlas-expression-exploration/Dockerfile -t mcp-atlas-expression-exploration:0.0.3 .
FROM mambaorg/micromamba:2.10.0-debian12-slim
LABEL org.opencontainers.image.source="https://github.com/lijinbio/aiscientist-mcp" \
      org.opencontainers.image.description="MCP server atlas-expression-exploration" \
      org.opencontainers.image.licenses="MIT"
# Nimbus Sans (Helvetica metrics) so that PNG text and layout match the Helvetica PDF text.
USER root
RUN apt-get update && apt-get install -y --no-install-recommends fonts-urw-base35 \
    && rm -rf /var/lib/apt/lists/*
USER $MAMBA_USER
RUN micromamba install -y -n base -c conda-forge \
        python=3.13.18 scanpy=1.12.5 anndata=0.13.4 pyarrow=26.0.0 mcp=2.3.0 \
    && micromamba clean -a -y
COPY --chown=$MAMBA_USER:$MAMBA_USER packages/common /opt/aiscientist-mcp/packages/common
COPY --chown=$MAMBA_USER:$MAMBA_USER packages/atlas-expression-exploration /opt/aiscientist-mcp/packages/atlas-expression-exploration
ARG MAMBA_DOCKERFILE_ACTIVATE=1
RUN pip install --no-deps --no-build-isolation /opt/aiscientist-mcp/packages/common \
        /opt/aiscientist-mcp/packages/atlas-expression-exploration
ENV PATH=/opt/conda/bin:$PATH
# Fail the build if the figure font (Helvetica, else Nimbus Sans) does not resolve to Nimbus Sans.
RUN python -c "from matplotlib import font_manager as fm; \
p = fm.findfont(fm.FontProperties(family=['Helvetica', 'Nimbus Sans']), fallback_to_default=False); \
print(p); assert 'NimbusSans' in p, p"
CMD ["atlas-expression-exploration-mcp"]
