FROM rust:1-bookworm AS exporter
RUN apt-get update && apt-get install -y --no-install-recommends git protobuf-compiler openssl libssl-dev pkg-config clang cmake && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git init . && git remote add origin https://github.com/stek29/export-findmy.git && git fetch --depth 1 origin eb5a3a93116478e1c088dc90fa84e1bb30ee232e && git checkout FETCH_HEAD
RUN cargo build --release

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates libssl3 curl p7zip-full && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN git init /opt/os-findmy && cd /opt/os-findmy && git remote add origin https://github.com/sacca97/os-findmy-e.git && git fetch --depth 1 origin 374d83854f657a7fdca45f813387d99d491db0e2 && git checkout FETCH_HEAD && rm -rf .git
COPY --from=exporter /src/target/release/export-findmy /usr/local/bin/export-findmy
RUN groupadd -g 1000 airtag && useradd -u 1000 -g 1000 -M airtag
COPY . .
RUN python patch_macadi.py /opt/os-findmy
USER 1000:1000
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=3)"
CMD ["gunicorn","--bind","0.0.0.0:8787","--workers","1","--threads","4","--timeout","60","app:app"]
