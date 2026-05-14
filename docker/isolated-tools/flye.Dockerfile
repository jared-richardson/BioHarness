FROM --platform=linux/amd64 ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH=/opt/tool-env/bin:/opt/conda/bin:${PATH}

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bzip2 \
        ca-certificates \
        curl \
        git \
        tini \
    && rm -rf /var/lib/apt/lists/*

RUN curl -L https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh -o /tmp/miniforge.sh \
    && bash /tmp/miniforge.sh -b -p /opt/conda \
    && rm -f /tmp/miniforge.sh

RUN /opt/conda/bin/conda config --system --add channels conda-forge \
    && /opt/conda/bin/conda config --system --add channels bioconda \
    && /opt/conda/bin/conda config --system --set channel_priority strict \
    && /opt/conda/bin/mamba create -y -p /opt/tool-env python=3.11 flye=2.9.6 minimap2 \
    && /opt/conda/bin/conda clean -afy

ENTRYPOINT ["/usr/bin/tini", "--"]
