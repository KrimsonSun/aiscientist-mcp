FROM python:3.12-slim-bookworm
LABEL org.opencontainers.image.source="https://github.com/lijinbio/aiscientist-mcp" \
      org.opencontainers.image.description="Grounded HPO mapping and LIRICAL differential diagnosis MCP server" \
      org.opencontainers.image.licenses="MIT"
ARG LIRICAL_VERSION=2.4.1
RUN apt-get update && apt-get install -y --no-install-recommends openjdk-17-jre-headless curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/lirical \
    && curl -fSL --retry 3 -o /tmp/lirical.zip "https://github.com/TheJacksonLaboratory/LIRICAL/releases/download/v${LIRICAL_VERSION}/lirical-cli-${LIRICAL_VERSION}-distribution.zip" \
    && unzip -q /tmp/lirical.zip -d /opt/lirical \
    && rm /tmp/lirical.zip \
    && printf '#!/bin/sh\nexec java -jar /opt/lirical/lirical-cli-%s/lirical-cli-%s.jar "$@"\n' "$LIRICAL_VERSION" "$LIRICAL_VERSION" > /usr/local/bin/lirical \
    && chmod +x /usr/local/bin/lirical \
    && lirical --help >/dev/null
WORKDIR /opt/aiscientist-mcp
RUN pip install --no-cache-dir 'torch>=2' --index-url https://download.pytorch.org/whl/cpu
COPY packages/common packages/common
COPY packages/literature packages/literature
COPY packages/phenotype-diagnosis packages/phenotype-diagnosis
RUN pip install --no-cache-dir ./packages/common './packages/literature[full]' ./packages/phenotype-diagnosis \
    && python -c "from aiscientist_mcp.phenotype_diagnosis.server import server; from aiscientist_mcp.phenotype_diagnosis.hpo.index import get_index; assert get_index().validate('HP:0000662')['status'] == 'ok'"
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PHENOTYPE_LITERATURE_COMMAND=literature-mcp
CMD ["phenotype-diagnosis-mcp"]
