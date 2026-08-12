FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /tmp/renforge-build
COPY pyproject.toml ./
RUN RENFORGE_VERSION="$(python -c 'import pathlib, tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"])')" \
    && python -m pip install --no-cache-dir "renforge==${RENFORGE_VERSION}" \
    && rm -rf /tmp/renforge-build

RUN useradd --create-home --uid 10001 renforge \
    && mkdir /workspace \
    && chown renforge:renforge /workspace

USER renforge
WORKDIR /workspace

ENTRYPOINT ["renforge"]
CMD ["serve"]
