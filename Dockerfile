# capcut-mcp: stdio MCP server for CapCut drafts (VectCutAPI pinned + safety wrapper)
# Build:  ./build.sh   (tags capcut-mcp:<WRAPPER_VERSION> and capcut-mcp:local)
# Run:    invoked by Claude Desktop via `docker run -i --rm ...` (see README.md)
FROM python:3.12-slim

ARG VECTCUT_SHA=d14e70749c9331424ab816a402bb45417c50cf68
ARG WRAPPER_VERSION=dev
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.title="capcut-mcp" \
      org.opencontainers.image.version="${WRAPPER_VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      io.vectcut.upstream.sha="${VECTCUT_SHA}"
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp \
    IMAGEIO_USERDIR=/tmp \
    PYTHONUTF8=1 \
    VECTCUT_UPSTREAM_SHA=${VECTCUT_SHA}

# ffprobe is used by VectCutAPI for media metadata; ffmpeg pulls it in.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Locked, hash-pinned Python deps (uv pip compile --generate-hashes requirements.in)
COPY requirements.lock /app/requirements.lock
COPY requirements-asr.lock /app/requirements-asr.lock
RUN pip install --require-hashes -r /app/requirements.lock
RUN pip install --no-cache-dir -r /app/requirements-asr.lock

# Upstream source, vendored as a deterministic `git archive` of the pinned commit
# (extracts to /app/VectCutAPI/ because the archive was created with --prefix=VectCutAPI/).
ADD vendor/VectCutAPI-d14e7074.tar.gz /app/

# Our container config, CapCut 9.3 template and the wrapper.
COPY config.json /app/VectCutAPI/config.json
COPY template_capcut_9/ /app/VectCutAPI/template_capcut_9/
COPY platform_capcut_9.json entry.py timeline_edit.py audio_alignment.py /app/
COPY tools/fetchlayer_tiktok.py /app/tools/fetchlayer_tiktok.py

RUN useradd --uid 10001 --create-home --home-dir /tmp/appuser appuser \
 && chown -R appuser:appuser /app

USER appuser
# /drafts (rw) and /media (ro) are bind-mounted at run time; no ports are exposed.
ENTRYPOINT ["python", "/app/entry.py"]
