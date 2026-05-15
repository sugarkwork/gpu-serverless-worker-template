# gpu-serverless-worker-template

Reference Python worker for the [gpu-serverless control plane](https://github.com/sugarkwork/gpu-serverless-control-plane)
(a RunPod-Serverless-style API backed by Vast.ai). Fork this repo, replace
`worker/handler.py` with your model code, build, push to any container
registry, then point an endpoint at it.

Conforms to the **standard worker contract**:

| Method | Path           | Purpose                                                 |
| ------ | -------------- | ------------------------------------------------------- |
| GET    | `/health`      | 200 + `{"ready": true}` when ready                      |
| POST   | `/run`         | `{"input": ...}` → `{"id": "...", "status": "queued"}` |
| GET    | `/status/{id}` | `{status, progress?, output?, error?}`                  |
| POST   | `/cancel/{id}` | cancel an in-flight job                                 |

The container also joins your Tailscale tailnet on boot (using the
`TS_AUTHKEY` env var injected by the control plane) so the VPS can reach
the worker without exposing a public port.

## What you change

Replace `worker/handler.py` with your model code. The signature is:

```python
async def handler(input: dict, progress_update: Callable[[dict], None]) -> dict:
    ...
    progress_update({"step": 1, "of": 10})   # surfaces via /status
    ...
    return {"result": "..."}
```

Errors raised from `handler(...)` become `status="failed"` with `error`
populated. `asyncio.CancelledError` is treated as a clean cancel.

## Build & push

```bash
docker build -t docker.io/<your-ns>/gpu-serverless-template:echo-v1 .
docker push  docker.io/<your-ns>/gpu-serverless-template:echo-v1
```

Then add an endpoint in `endpoints.toml`:

```toml
[[endpoints]]
id = "my-endpoint"
api_keys = ["sk-..."]

  [endpoints.bootstrap]
  mode = "docker"
  docker_image = "docker.io/<your-ns>/gpu-serverless-template:echo-v1"

  [endpoints.contract]
  worker_port = 8000

  [endpoints.pool]
  max_workers = 2
  scale_wait_sec = 4

  [endpoints.vast]
  max_price = 0.3
  num_gpus = 0       # CPU-only is fine for the echo template
  disk_gb  = 16
```

## Env vars the container reads

| Variable        | Default       | Notes                                                 |
| --------------- | ------------- | ----------------------------------------------------- |
| `TS_AUTHKEY`    | (required)    | Tailscale auth key. Inject via control plane.         |
| `TS_HOSTNAME`   | `gs-worker-*` | Tailscale node name (control plane uses it to find IP) |
| `WORKER_PORT`   | `8000`        | Worker listen port                                    |
| `MAX_PARALLEL`  | `1`           | Concurrent jobs per worker                            |
| `WORKER_VERSION` | `echo-v1`    | Returned in `/health` and `/info`                     |
| `WORKER_LOG_LEVEL` | `INFO`     |                                                       |

## Privileges

Tailscale in kernel mode needs `/dev/net/tun` and `CAP_NET_ADMIN`. Vast.ai
instances usually provide both for custom-image launches. If unavailable
the entrypoint falls back to userspace mode (with `tailscale serve` to
publish the listener on the tailnet IP) — slightly slower but functional.

## Local smoke test (no Tailscale)

```bash
docker build -t worker-local .
docker run --rm -p 8000:8000 worker-local

# in another terminal:
curl localhost:8000/health
curl -X POST localhost:8000/run -d '{"input":{"text":"hi","wait":2}}' -H 'Content-Type: application/json'
# → {"id":"...","status":"queued"}
curl localhost:8000/status/<id>
```
