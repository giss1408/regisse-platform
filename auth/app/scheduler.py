"""Periodic jobs of the auth service, run by the `scheduler` container (one loop, no extra dependency).

Every AUTH_SCHEDULER_INTERVAL_SECONDS (default 600): number changes whose security delay has passed take effect
(`accounts.apply_due_phone_changes`; sign-in also applies them lazily, this is the safety net for people who do not sign in).
A failing run is logged and retried at the next tick; the heartbeat file is touched after EVERY tick so the container healthcheck can tell
"alive and failing" (visible in the logs) from "dead". Counts only are logged, never numbers.
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from app.db import SessionLocal
from app.services import accounts

log = logging.getLogger("auth.scheduler")
HEARTBEAT = Path(os.environ.get("AUTH_SCHEDULER_HEARTBEAT", "/tmp/auth-scheduler-heartbeat"))


def interval() -> int:
    return max(int(os.environ.get("AUTH_SCHEDULER_INTERVAL_SECONDS", "600")), 10)


async def tick() -> dict:
    """One pass of every job. Returns counts."""
    async with SessionLocal() as db:
        out = await accounts.apply_due_phone_changes(db)
        await db.commit()
    return {"phone_changes": out}


async def serve(iterations: int | None = None, sleep=asyncio.sleep) -> int:
    """Loops forever (or `iterations` times, for tests). Returns how many ticks failed."""
    failed = n = 0
    while iterations is None or n < iterations:
        try:
            log.info("tick: %s", json.dumps(await tick()))
        except Exception:  # a database blip must not kill the loop: log it and try again at the next tick
            failed += 1
            log.exception("tick failed")
        HEARTBEAT.write_text(str(int(time.time())))
        n += 1
        if iterations is None or n < iterations:
            await sleep(interval())
    return failed


def healthy(max_age_seconds: int | None = None) -> bool:
    """For the container healthcheck: the loop ticked within three intervals."""
    try:
        age = time.time() - int(HEARTBEAT.read_text())
    except (OSError, ValueError):
        return False
    return age < (max_age_seconds or interval() * 3)
