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
COPY --from=ghcr.io/astral-sh/uv:0.13.10@sha256:a3ea1357342c1a4bf9a0c53a3deb1b55379aefa037338d8113d5771efa01774f /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY secure_mlops ./secure_mlops
COPY configs ./configs
# Nothing in the image installs packages once it is built, so apt and its library are removed: a CVE in apt
# (CVE-2026-109518, 5 November) cannot reach an image that has no apt, and the fix does not wait for a new base.
# The base image's zlib1g carries CVE-2026-76844 and CVE-2026-85091 (HIGH, 8 November), and its libzstd1
# CVE-2026-124851 (HIGH, fixed in 1.5.7+dfsg-1+deb13u1); Debian's fixes are in its security archive before they are in a
# new python:3.12-slim, so the build takes the security upgrades before apt is removed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade zlib1g libzstd1 \
    && uv sync --frozen --no-dev --no-editable --compile-bytecode \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin pipeline \
    && dpkg --purge --force-remove-protected apt libapt-pkg7.0 \
    && rm -rf /var/lib/apt /var/cache/apt /etc/apt
ENV PATH="/app/.venv/bin:$PATH"

USER 10001:10001
WORKDIR /home/pipeline
ENTRYPOINT ["smlops"]
CMD ["--help"]
