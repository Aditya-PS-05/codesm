FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CODESM_DATA_DIR=/data/codesm

# git is needed by the session snapshot/undo layer. curl is useful
# for sanity checks inside the container but not required.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for faster dependency resolution.
RUN pip install uv

WORKDIR /app

# Copy only the files uv needs to resolve first so dependency layers
# can be cached independently of the source tree.
COPY pyproject.toml README.md ./
COPY codesm ./codesm

RUN uv pip install --system -e .

# A writable data directory for sessions, events, and the audit log.
RUN mkdir -p /data/codesm /workspace \
    && chmod 777 /data/codesm /workspace

WORKDIR /workspace

# Default entrypoint launches the TUI. Override with
#     docker run --rm -it codesm codesm eval <task.yaml>
# to run the eval subcommand instead.
ENTRYPOINT ["codesm"]
