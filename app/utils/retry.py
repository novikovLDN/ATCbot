"""
Centralized async retry utility for transient failures.

This module provides a retry mechanism for external I/O operations
that may fail transiently (network issues, database timeouts, etc.).

Retry policy:
- Exponential backoff with jitter
- Only retries on transient failures
- Preserves original exception on final failure
- No logging inside utility (caller handles logging)

STEP 1.1 - RUNTIME GUARDRAILS:
- No retries for domain errors → domain exceptions are raised immediately
- Retries only for transient infra errors → timeouts, connection resets
- Retries ONLY with backoff → exponential backoff with jitter
- Max retries: 2 (DEFAULT_RETRIES) → total 3 attempts (initial + 2 retries)

STEP 1.3 - EXTERNAL DEPENDENCIES POLICY:
- Transient exceptions: asyncpg.PostgresError, asyncio.TimeoutError, aiohttp.ClientError, httpx.HTTPError, ConnectionError, OSError
- Domain exceptions: NEVER retried → raised immediately
- Validation errors: NEVER retried → raised immediately
"""

import asyncio
import random
from typing import Callable, Type, Tuple, Union, Any
import asyncpg
import aiohttp
import httpx


# Default retry configuration
DEFAULT_RETRIES = 2
DEFAULT_BASE_DELAY = 1.0
DEFAULT_MAX_DELAY = 10.0


# Transient exceptions that should be retried
TRANSIENT_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    asyncpg.PostgresError,
    asyncio.TimeoutError,
    aiohttp.ClientError,
    httpx.HTTPError,  # httpx network errors
    httpx.TimeoutException,  # httpx timeouts
    ConnectionError,
    OSError,  # Network errors
)


async def retry_async(
    fn: Callable[[], Any],
    *,
    retries: int = DEFAULT_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    retry_on: Tuple[Type[Exception], ...] = TRANSIENT_EXCEPTIONS,
) -> Any:
    """
    Retry an async function with exponential backoff.
    
    Args:
        fn: Async function to retry (callable that returns awaitable)
        retries: Number of retry attempts (default: 2, total attempts: 3)
        base_delay: Base delay in seconds for exponential backoff (default: 1.0)
        max_delay: Maximum delay in seconds (default: 10.0)
        retry_on: Tuple of exception types to retry on (default: transient exceptions)
        
    Returns:
        Result of the function call
        
    Raises:
        Original exception if all retries fail
        Non-retryable exceptions are raised immediately
    """
    last_exception = None
    
    for attempt in range(retries + 1):
        try:
            # Call the function
            if asyncio.iscoroutinefunction(fn):
                result = await fn()
            else:
                # Handle callable that returns coroutine
                coro = fn()
                if asyncio.iscoroutine(coro):
                    result = await coro
                else:
                    result = coro
            
            return result
            
        except Exception as e:
            last_exception = e
            
            # Check if this exception should be retried
            if not isinstance(e, retry_on):
                # Non-retryable exception - raise immediately
                raise
            
            # Check if we have retries left
            if attempt >= retries:
                # No more retries - raise original exception
                raise
            
            # Calculate delay with exponential backoff and jitter
            delay = min(base_delay * (2 ** attempt), max_delay)
            # Add jitter (±20%)
            jitter = delay * 0.2 * (random.random() * 2 - 1)
            delay = max(0, delay + jitter)  # Ensure non-negative
            
            # Wait before retry
            await asyncio.sleep(delay)
    
    # Should never reach here, but raise last exception if we do
    if last_exception:
        raise last_exception
    
    raise RuntimeError("retry_async: unexpected end of retry loop")
