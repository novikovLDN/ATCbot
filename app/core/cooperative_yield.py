"""
Event loop protection: cooperative yield for long-running worker loops.

Prevents event loop starvation when workers process many items.
Use inside loops iterating over DB results or UUIDs.
"""
import asyncio


async def cooperative_yield() -> None:
    """Yield control to event loop. Use every N iterations in long loops."""
    await asyncio.sleep(0)
