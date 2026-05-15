"""Replace this module with your own logic.

`handler(input, progress_update)` is called per job. It receives the user's
input payload (dict) and a `progress_update(dict)` callback you can call
during long-running work to surface progress over `/status/{id}`.

The default implementation is an echo + sleep handler — useful for verifying
the control plane (autoscale, dispatch, /status streaming, idle teardown)
without paying for a real GPU instance.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable


ProgressFn = Callable[[dict[str, Any]], None]


async def handler(input: dict[str, Any], progress_update: ProgressFn) -> dict[str, Any]:
    """Default echo/sleep handler.

    Input:
      {
        "text":   "anything",      # echoed back
        "wait":   3.5,             # seconds to "work"
        "fail":   false,           # if true, raises after `wait` seconds
        "steps":  5                # number of progress updates to emit
      }

    Output:
      {
        "echo": "...",
        "slept_sec": 3.5,
        "started_at": 1700000000.123,
        "completed_at": 1700000003.623
      }
    """
    text = input.get("text", "")
    wait = float(input.get("wait", 1.0))
    fail = bool(input.get("fail", False))
    steps = max(1, int(input.get("steps", 5)))
    started_at = time.time()

    interval = wait / steps if steps > 0 else 0
    for i in range(steps):
        progress_update(
            {
                "step": i + 1,
                "of": steps,
                "elapsed_sec": time.time() - started_at,
            }
        )
        await asyncio.sleep(interval)

    if fail:
        raise RuntimeError(f"requested failure after {wait}s")

    return {
        "echo": text,
        "slept_sec": wait,
        "started_at": started_at,
        "completed_at": time.time(),
    }
