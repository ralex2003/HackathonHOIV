import asyncio
from typing import Callable, Any

class AsyncRateLimiter:
    def __init__(self, rate_limit: int = 3):
        self.semaphore = asyncio.Semaphore(rate_limit)
        self.rate_limit = rate_limit
    
    async def __aenter__(self):
        await self.semaphore.acquire()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Small delay to respect rate limits
        await asyncio.sleep(0.35)  # ~3 requests per second
        self.semaphore.release()
