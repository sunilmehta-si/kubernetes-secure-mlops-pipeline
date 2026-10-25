# The image every pipeline step runs in: this package and its pinned dependencies, nothing else.
# Base pinned by digest (python:3.12-slim, multi-arch index), so a rebuild cannot silently change it.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MLFLOW_DISABLE_AGENT_HINT=1 \
    SMLOPS_RISK_DIR=/app/configs/models

WORKDIR /app
# uv installs exactly what uv.lock says; the uv binary comes from its image, pinned by digest.
COPY --from=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY secure_mlops ./secure_mlops
COPY configs ./configs
# The urllib3 CVE override (kfp pins 2.7.0) lives in pyproject.toml under [tool.uv], so it is part of the lock.
RUN uv sync --frozen --no-dev --no-editable --compile-bytecode \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin pipeline
ENV PATH="/app/.venv/bin:$PATH"

USER 10001:10001
WORKDIR /home/pipeline
ENTRYPOINT ["smlops"]
CMD ["--help"]
