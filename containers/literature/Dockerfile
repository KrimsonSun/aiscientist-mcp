FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/lijinbio/aiscientist-mcp" \
      org.opencontainers.image.description="Biomedical literature MCP server" \
      org.opencontainers.image.licenses="MIT"
WORKDIR /opt/aiscientist-mcp
RUN pip install --no-cache-dir 'torch>=2' --index-url https://download.pytorch.org/whl/cpu
COPY packages/common packages/common
COPY packages/literature packages/literature
RUN pip install --no-cache-dir ./packages/common './packages/literature[full]' \
    && python -c "import paperqa, sentence_transformers, pymupdf; from aiscientist_mcp.literature.server import server"
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
CMD ["literature-mcp"]
