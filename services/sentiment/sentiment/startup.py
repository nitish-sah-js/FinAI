import asyncio
from .ticker_map import get_index
from .hinglish   import init_hinglish


async def startup():
    print("[startup] building indexes in parallel...")
    await asyncio.gather(
        get_index(),
        init_hinglish(),
    )
    print("[startup] all indexes ready")