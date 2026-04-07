"""Shared utilities for celery worker tasks
Celery workers are sync processes.We run async service code by maintaining
a single event loop per worker process

Pattern:
  - _get_worker_loop() returns the same loop for the worker's lifetime
  - Infrastructure (engine,redis pool) is created once per worker process via module_level singletons,not inside each task func

"""

from __future__ import annotations
import asyncio
import threading


# per-thread loop storage
_loop_lock = threading.Lock()  # prevent race condition for 2 threads
_worker_loops: dict[
    int, asyncio.AbstractEventLoop
] = {}  # ensures each thread gets one loop


def get_worker_loop() -> asyncio.AbstractEventLoop:
    """Get or create a persistent event loop for the current thread
    Creates a new loop on first call per thread ,reuses it thereafter
    """
    tid = threading.get_ident()
    with _loop_lock:
        if tid not in _worker_loops or _worker_loops[tid].is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _worker_loops[tid] = loop
        return _worker_loops[tid]


def run_async(coro) -> object:
    """Run an async coroutine from a syncronous Celery task"""

    return get_worker_loop().run_until_complete(coro)
