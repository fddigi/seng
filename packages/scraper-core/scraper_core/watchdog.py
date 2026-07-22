"""Run a single scraper source with a wall-clock budget, so a SLOW-BUT-FINITE
source never blocks an entire multi-source pipeline run indefinitely.

Built after a real production incident (PLAGG): an unexplained multi-hour hang
was never root-caused despite a 36-iteration stress test, and was worked
around with an external watchdog rather than a targeted fix. This gives every
project on this boilerplate that same backstop for free, instead of each one
needing to build its own ad-hoc timeout wrapper around a multi-source loop.

IMPORTANT, confirmed by direct testing (see PA SPEAKERS Fund 18 - an earlier
version of this docstring claimed otherwise, which was FALSE): this does NOT
protect against a genuinely, infinitely hung `fn()` (e.g. a blocked socket
read with no timeout of its own, simulated with `time.sleep(9999)`). Python
cannot forcibly kill a running thread, and `concurrent.futures.thread`
registers an `atexit` hook that joins EVERY thread ever created by ANY
ThreadPoolExecutor in the process before the interpreter can exit - so an
abandoned, truly-infinite background thread keeps the WHOLE PROCESS from
exiting on its own, even though `run_with_timeout()` itself correctly raises
SourceTimeoutError and returns control to its caller. Use this for a source
that is slow-but-eventually-returns (e.g. many retries over a few minutes),
not as protection against a genuinely infinite hang - for that, run the
source in a `multiprocessing.Process` and call `.terminate()`/`.kill()` on
timeout instead, since a process (unlike a thread) can actually be killed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class SourceTimeoutError(Exception):
    """Raised when a source function exceeds its allotted wall-clock budget."""


def run_with_timeout(fn: Callable[[], T], *, timeout_seconds: float, source_name: str) -> T:
    """Runs fn() with a wall-clock timeout budget, returning its result or
    raising SourceTimeoutError.

    LIMITATION, by design (see module docstring for the confirmed-by-testing
    details): this guarantees THIS FUNCTION returns control to its caller
    after timeout_seconds - it does NOT guarantee the process can exit
    afterwards if fn() is genuinely, infinitely blocked, and it does NOT kill
    fn() itself. Safe to rely on for a source that's slow but eventually
    finishes; not a substitute for request-level timeouts on the underlying
    HTTP/Playwright calls, which remain the first line of defense.

    Typical usage in a multi-source pipeline:

        for name, module in SOURCE_MODULES.items():
            try:
                listings = run_with_timeout(
                    lambda: module.fetch(config), timeout_seconds=300, source_name=name
                )
            except SourceTimeoutError:
                continue  # already logged; move on to the next source
            except Exception:
                logger.exception("%s: source failed, skipping", name)
                continue
            ...
    """
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError as exc:
        logger.warning(
            "watchdog: source '%s' exceeded %.0fs budget - skipping, continuing with remaining",
            source_name,
            timeout_seconds,
        )
        raise SourceTimeoutError(
            f"source '{source_name}' exceeded {timeout_seconds}s timeout"
        ) from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=False)
