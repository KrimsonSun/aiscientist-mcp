# ghcr.io/<owner>/mcp-retigene-rag:<version>
# Build from the repository root:
#   docker build -f containers/retigene-rag/Dockerfile -t mcp-retigene-rag:0.0.1 .
# The corpus index is not in the image: mount it read-only and set RETIGENE_INDEX. The embedding model is
# read from HF_HOME (mount a cache that holds NeuML/pubmedbert-base-embeddings, or allow the download).
FROM mambaorg/micromamba:2.10.0-debian12-slim
LABEL org.opencontainers.image.source="https://github.com/lijinbio/aiscientist-mcp" \
      org.opencontainers.image.description="MCP server retigene-rag" \
      org.opencontainers.image.licenses="MIT"
RUN micromamba install -y -n base -c conda-forge python=3.13.18 mcp=2.3.0 numpy=2.5.4 pip \
    && micromamba clean -a -y
ARG MAMBA_DOCKERFILE_ACTIVATE=1
# CPU-only torch for the query embeddings; the GPU build is not needed and is ten times larger.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.15.0 \
    && pip install --no-cache-dir anthropic==1.13.0 sentence-transformers==6.2.0
COPY --chown=$MAMBA_USER:$MAMBA_USER packages/common /opt/aiscientist-mcp/packages/common
COPY --chown=$MAMBA_USER:$MAMBA_USER packages/retigene-rag /opt/aiscientist-mcp/packages/retigene-rag
RUN pip install --no-deps --no-build-isolation /opt/aiscientist-mcp/packages/common \
        /opt/aiscientist-mcp/packages/retigene-rag
ENV PATH=/opt/conda/bin:$PATH
RUN python -c "import anthropic, sentence_transformers, aiscientist_mcp.retigene_rag.server"
CMD ["retigene-rag-mcp"]
