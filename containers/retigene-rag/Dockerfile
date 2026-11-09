# ghcr.io/<owner>/mcp-retigene-rag:<version>
# Build from the repository root:
#   docker build -f containers/retigene-rag/Dockerfile -t mcp-retigene-rag:0.0.1 .
# The corpus index is not in the image: mount a version of the shared dataset retigene read-only and set
# RETIGENE_INDEX to it. The query embedding model (the revision that made the chunk vectors) is in the image.
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
# NeuML/pubmedbert-base-embeddings (Apache-2.0) at the revision of the RetiGene chunk vectors.
ENV HF_HOME=/opt/conda/share/huggingface
RUN python -c "from huggingface_hub import snapshot_download; \
snapshot_download('NeuML/pubmedbert-base-embeddings', revision='b79526d6ef3645e0df4530322e266f24c829f5ef')" \
    && chmod -R a+rX "$HF_HOME"
COPY --chown=$MAMBA_USER:$MAMBA_USER packages/common /opt/aiscientist-mcp/packages/common
COPY --chown=$MAMBA_USER:$MAMBA_USER packages/retigene-rag /opt/aiscientist-mcp/packages/retigene-rag
RUN pip install --no-deps --no-build-isolation /opt/aiscientist-mcp/packages/common \
        /opt/aiscientist-mcp/packages/retigene-rag
ENV PATH=/opt/conda/bin:$PATH HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
# Fail the build if the server does not import or the embedding model does not load offline.
RUN python -c "import aiscientist_mcp.retigene_rag.server; from aiscientist_mcp.retigene_rag.search import embedder; \
v = embedder()(['haploinsufficiency of PAX6 causes aniridia']); assert v.shape == (1, 768), v.shape"
CMD ["retigene-rag-mcp"]
