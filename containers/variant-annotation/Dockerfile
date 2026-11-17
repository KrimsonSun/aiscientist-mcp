# Build from the repository root; pair VEP release 112 with matching read-only caches.
FROM mambaorg/micromamba:2.10.0-debian12-slim AS micromamba
FROM ensemblorg/ensembl-vep:release_112.0
LABEL org.opencontainers.image.source="https://github.com/lijinbio/aiscientist-mcp" \
      org.opencontainers.image.description="Offline VEP 112 MCP server" \
      org.opencontainers.image.licenses="MIT"
USER root
RUN apt-get update && apt-get install -y --no-install-recommends bcftools \
    && rm -rf /var/lib/apt/lists/*
# The VEP base is Ubuntu 22.04. Use an isolated Python 3.12 runtime, leaving its Perl stack intact.
COPY --from=micromamba /bin/micromamba /usr/local/bin/micromamba
ENV MAMBA_ROOT_PREFIX=/opt/micromamba
RUN micromamba create -y -p /opt/mcp-env -c conda-forge \
        python=3.12 mcp=2.3.0 setuptools=84.2.1 pip \
    && micromamba clean -a -y
COPY packages/common /opt/aiscientist-mcp/packages/common
COPY packages/variant-annotation /opt/aiscientist-mcp/packages/variant-annotation
RUN /opt/mcp-env/bin/pip install --no-deps --no-build-isolation \
        /opt/aiscientist-mcp/packages/common /opt/aiscientist-mcp/packages/variant-annotation \
    && /opt/vep/src/ensembl-vep/vep --help > /dev/null \
    && bcftools --version > /dev/null \
    && tabix --version > /dev/null
ENV PATH=/opt/mcp-env/bin:/opt/vep/src/ensembl-vep:$PATH
USER vep
WORKDIR /opt/vep
CMD ["variant-annotation-mcp"]
