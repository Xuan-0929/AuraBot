"""Run the desktop-owned Uvicorn server with a private, cross-platform shutdown pipe.

Electron keeps stdin open for this process and closes it on Apply/quit. A daemon reader requests
Uvicorn's normal shutdown on EOF, including when Electron crashes, so FastAPI lifespan can close
Profiles, voice and Task processes on Windows without relying on POSIX signals. No HTTP shutdown
endpoint is exposed. The standalone uvicorn development entry remains available independently.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from typing import TextIO

import uvicorn


async def serve(server: uvicorn.Server, control: TextIO) -> None:
    loop = asyncio.get_running_loop()

    def wait_for_parent() -> None:
        # Only this inherited pipe may request shutdown; ignore any incidental input.
        try:
            while control.read(1):
                pass
        finally:
            if not loop.is_closed():
                loop.call_soon_threadsafe(setattr, server, "should_exit", True)

    threading.Thread(target=wait_for_parent, name="desktop-shutdown", daemon=True).start()
    await server.serve()
    # EOF during startup makes Uvicorn return before its shutdown() call.
    if server.started and not server.lifespan.shutdown_event.is_set():
        await server.shutdown()


def main() -> None:
    server = uvicorn.Server(uvicorn.Config("backend.main:app", host="127.0.0.1", port=6401, lifespan="on"))
    asyncio.run(serve(server, sys.stdin))


if __name__ == "__main__":
    main()
