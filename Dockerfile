FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime

# Apt packages — own layer so pip changes don't re-run apt
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl gosu tzdata \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# App dependencies — only rebuilds when requirements.txt changes
COPY requirements.txt /
RUN --mount=type=cache,target=/root/.cache/pip \
    python3 -m pip install -U -r /requirements.txt

WORKDIR /subgen

# App files last — changes here don't bust the layers above
COPY launcher.py subgen.py language_code.py scan_index.py /subgen/
COPY subgen_startup_scan /subgen/subgen_startup_scan

RUN mkdir -p /cache && chmod 777 /cache

ENV XDG_CACHE_HOME=/cache \
    HF_HOME=/cache/huggingface \
    MPLCONFIGDIR=/cache/matplotlib \
    PYTHONUNBUFFERED=1

COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "launcher.py"]
