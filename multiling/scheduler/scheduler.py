"""Scheduler — see __init__."""
import logging
import sys
import time, threading
from typing import Callable

logger = logging.getLogger(__name__)

_jobs = []
_lock = threading.Lock()


def _job_store():
    module = sys.modules.get(__name__)
    if module is None:
        return _jobs
    store = getattr(module, "_jobs", None)
    if store is None:
        store = []
        setattr(module, "_jobs", store)
    return store


def schedule(cron, fn, name=None):
    with _lock:
        _job_store().append({"cron": cron, "fn": fn, "name": name or fn.__name__, "last": 0})


def list_jobs():
    with _lock:
        return list(_job_store())


class Scheduler:
    def __init__(self):
        self._running = False

    def start(self):
        self._running = True
        while self._running:
            now = time.time()
            for j in _job_store():
                if now - j["last"] >= 60:
                    try:
                        j["fn"]()
                        j["last"] = now
                    except Exception:
                        logger.debug("scheduled job %r failed", j["name"], exc_info=True)
            time.sleep(1)

    def stop(self):
        self._running = False
