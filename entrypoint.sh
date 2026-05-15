#!/usr/bin/env bash
# Worker container entrypoint.
#
# Order:
#   1. (optional) Start tailscaled and join the tailnet with TS_AUTHKEY.
#   2. Hand off (exec) to uvicorn running the FastAPI worker.
#
# Why both modes:
#   The container needs an inbound HTTP listener reachable from the
#   gpu.sugar-knight.com VPS. Tailscale gives us that without exposing
#   ports publicly. Kernel-mode tailscaled (default) requires /dev/net/tun
#   + CAP_NET_ADMIN; if either is missing we fall back to userspace mode
#   with `tailscale serve` to publish ${WORKER_PORT} on the tailnet IP.

set -euo pipefail

WORKER_PORT="${WORKER_PORT:-8000}"

ts_sock=/var/run/tailscale/tailscaled.sock
ts_state=/var/lib/tailscale
log_dir=/var/log
mkdir -p "$(dirname "$ts_sock")" "$ts_state" "$log_dir"

start_tailscaled() {
    if [ -n "${TS_AUTHKEY:-}" ]; then
        mkdir -p /dev/net
        [ -c /dev/net/tun ] || mknod /dev/net/tun c 10 200 2>/dev/null || true

        if [ -c /dev/net/tun ] && ip tuntap add mode tun name ts_probe 2>/dev/null; then
            ip tuntap del mode tun name ts_probe 2>/dev/null || true
            echo "[entrypoint] tailscaled: kernel mode"
            tailscaled \
                --statedir="$ts_state" \
                --socket="$ts_sock" \
                >"$log_dir/tailscaled.log" 2>&1 &
            ts_mode=kernel
        else
            echo "[entrypoint] tailscaled: userspace mode (no /dev/net/tun)"
            tailscaled \
                --tun=userspace-networking \
                --statedir="$ts_state" \
                --socket="$ts_sock" \
                >"$log_dir/tailscaled.log" 2>&1 &
            ts_mode=userspace
        fi

        # Wait for the socket to appear.
        for _ in $(seq 1 30); do
            [ -S "$ts_sock" ] && break
            sleep 0.5
        done

        tailscale --socket="$ts_sock" up \
            --authkey="$TS_AUTHKEY" \
            --hostname="${TS_HOSTNAME:-gs-worker-$RANDOM}" \
            --accept-routes=false \
            >"$log_dir/ts-up.log" 2>&1
        TS_IP=$(tailscale --socket="$ts_sock" ip -4 | head -1 || true)
        echo "[entrypoint] tailscale up: ip=$TS_IP mode=$ts_mode host=${TS_HOSTNAME:-?}"

        if [ "$ts_mode" = "userspace" ]; then
            # Expose the worker port on the tailnet via `tailscale serve`.
            tailscale --socket="$ts_sock" serve --bg --https=443 off 2>/dev/null || true
            tailscale --socket="$ts_sock" serve --bg \
                --tcp="$WORKER_PORT" "tcp://127.0.0.1:$WORKER_PORT" \
                >"$log_dir/ts-serve.log" 2>&1 || \
                echo "[entrypoint] WARN: tailscale serve failed; userspace inbound may not work"
        fi
    else
        echo "[entrypoint] TS_AUTHKEY unset — skipping Tailscale (worker only reachable on container localhost)"
    fi
}

start_tailscaled

# Hand control to uvicorn. PID 1 so the container exits if uvicorn dies.
echo "[entrypoint] starting worker on 0.0.0.0:$WORKER_PORT"
exec uvicorn worker.main:app --host 0.0.0.0 --port "$WORKER_PORT" --workers 1
