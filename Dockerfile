# The image every pipeline step runs in: this package and its pinned dependencies, nothing else.
# Base pinned by digest (python:3.12-slim, multi-arch index), so a rebuild cannot silently change it.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MLFLOW_DISABLE_AGENT_HINT=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY secure_mlops ./secure_mlops
COPY configs ./configs
RUN pip install . && useradd --uid 10001 --create-home --shell /usr/sbin/nologin pipeline

USER 10001:10001
WORKDIR /home/pipeline
ENTRYPOINT ["smlops"]
CMD ["--help"]
