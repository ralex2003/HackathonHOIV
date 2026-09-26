import time
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class SimpleCache:
    """Simple in-memory cache with TTL-based expiry."""

    def __init__(self, ttl: int = 3600):
        self.cache: Dict[str, tuple] = {}
        self.ttl = ttl
        logger.info("SimpleCache initialized with TTL=%ds", ttl)

    def get(self, key: str) -> Optional[Any]:
        if key in self.cache:
            value, timestamp = self.cache[key]
            age = time.time() - timestamp
            if age < self.ttl:
                logger.debug("Cache HIT for key '%s' (age=%.1fs)", key, age)
                return value
            else:
                logger.debug("Cache EXPIRED for key '%s' (age=%.1fs > TTL=%ds)", key, age, self.ttl)
                del self.cache[key]
        else:
            logger.debug("Cache MISS for key '%s'", key)
        return None

    def set(self, key: str, value: Any) -> None:
        self.cache[key] = (value, time.time())
        logger.debug("Cache SET for key '%s'", key)

    def clear(self) -> None:
        count = len(self.cache)
        self.cache.clear()
        logger.info("Cache cleared (%d entries removed)", count)

    def stats(self) -> dict:
        """Return cache statistics for debugging."""
        return {"entries": len(self.cache), "ttl": self.ttl}


# Global cache instance
cache = SimpleCache(ttl=3600)  # 1 hour TTL
logger.info("Global cache instance created")
