FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY src/requirements.txt /app/requirements.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    python3 -m pip install -r /app/requirements.txt

COPY src/server.py /app/server.py

EXPOSE 8008

ENV PORT=8008 \
    HOST=0.0.0.0

CMD ["python3", "/app/server.py"]
