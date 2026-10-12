"""Wait for a bounded blocking operation before acknowledging cooperative cancellation."""

import asyncio


async def run_blocking(function, *args):
    work = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        # Cancelling to_thread alone does not stop its OS thread.
        await asyncio.gather(work, return_exceptions=True)
        raise
