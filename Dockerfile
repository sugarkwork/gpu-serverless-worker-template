# gpu.sugar-knight.com worker template
#
# Buildable from this directory:
#   docker build -t docker.io/<your-namespace>/gpu-serverless-template:echo-v1 .
#   docker push  docker.io/<your-namespace>/gpu-serverless-template:echo-v1
#
# To extend with real GPU code, derive FROM this image (or copy the worker/
# directory) and replace `worker/handler.py`.

FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    WORKER_PORT=8000 \
    WORKER_VERSION=echo-v1

# Tailscale + minimal tooling (iproute2 for `ip tuntap`, ca-certs for HTTPS).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        iproute2 \
        iptables \
    && curl -fsSL https://tailscale.com/install.sh | sh \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip \
    && pip install "fastapi==0.115.4" "uvicorn[standard]==0.32.0"

WORKDIR /app
COPY worker/ /app/worker/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

CMD ["/entrypoint.sh"]
