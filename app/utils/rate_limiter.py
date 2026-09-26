import asyncio
import time
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class AsyncRateLimiter:
    """Limits concurrent outbound requests and spaces them out.

    The semaphore is created lazily per event loop. Holding one across loops
    is a bug: once the semaphore has to block, asyncio binds it to the running
    loop, and reusing it from a different loop raises
    "RuntimeError: ... is bound to a different event loop". Because Flask
    handlers here each run their own loop via asyncio.run, that made every
    request after the first fail -- and `asyncio.gather(return_exceptions=True)`
    swallowed the error, so the API just returned an empty graph.
    """

    def __init__(self, rate_limit: int = 3, min_interval: float = 0.35):
        self.rate_limit = max(1, rate_limit)
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._last_call = 0.0
        logger.info(
            "AsyncRateLimiter initialized: rate_limit=%d/min, min_interval=%.2fs",
            self.rate_limit, self.min_interval,
        )

    def _get_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._semaphore is None or self._loop is not loop:
                self._loop = loop
                self._semaphore = asyncio.Semaphore(self.rate_limit)
                logger.debug(
                    "New semaphore created for event loop (limit=%d)",
                    self.rate_limit,
                )
            return self._semaphore

    async def __aenter__(self):
        semaphore = self._get_semaphore()
        logger.debug("Rate limiter: acquiring semaphore...")
        await semaphore.acquire()
        if self.min_interval:
            loop = asyncio.get_running_loop()
            elapsed = loop.time() - self._last_call
            wait_time = self.min_interval - elapsed
            if wait_time > 0:
                logger.debug(
                    "Rate limiter: sleeping %.3fs to respect min_interval",
                    wait_time,
                )
                await asyncio.sleep(wait_time)
            self._last_call = loop.time()
            logger.debug(
                "Rate limiter: request completed (elapsed=%.3fs)", elapsed
            )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            self._get_semaphore().release()
            logger.debug("Rate limiter: semaphore released")
        except ValueError:
            # Semaphore was already over-released; nothing useful to do.
            logger.warning("Rate limiter: attempt to release already-free semaphore")
        return False
