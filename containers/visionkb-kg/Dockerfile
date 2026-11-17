# ghcr.io/<owner>/mcp-visionkb-kg:<version>
# Build from the repository root:
#   docker build -f containers/visionkb-kg/Dockerfile -t mcp-visionkb-kg:0.0.1 .
# Run (stdio): docker run -i --rm -e VISIONKB_API_URL=http://<api-host>:8770 \
#   -e VISIONKB_API_TOKEN_FILE=/run/secrets/visionkb_token -v /path/to/token:/run/secrets/visionkb_token:ro \
#   mcp-visionkb-kg:0.0.1
FROM mambaorg/micromamba:2.10.0-debian12-slim
LABEL org.opencontainers.image.source="https://github.com/lijinbio/aiscientist-mcp" \
      org.opencontainers.image.description="MCP server visionkb-kg" \
      org.opencontainers.image.licenses="MIT"
# setuptools: the build backend for the --no-build-isolation install (the base env has none).
RUN micromamba install -y -n base -c conda-forge python=3.13.18 mcp=2.3.0 setuptools=84.2.0 \
    && micromamba clean -a -y
COPY --chown=$MAMBA_USER:$MAMBA_USER packages/common /opt/aiscientist-mcp/packages/common
COPY --chown=$MAMBA_USER:$MAMBA_USER packages/visionkb-kg /opt/aiscientist-mcp/packages/visionkb-kg
ARG MAMBA_DOCKERFILE_ACTIVATE=1
RUN pip install --no-deps --no-build-isolation /opt/aiscientist-mcp/packages/common \
        /opt/aiscientist-mcp/packages/visionkb-kg \
    && python -c "import aiscientist_mcp.visionkb_kg.server"
ENV PATH=/opt/conda/bin:$PATH
CMD ["visionkb-kg-mcp"]
