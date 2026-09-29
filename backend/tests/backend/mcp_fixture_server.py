"""
tests/backend/mcp_fixture_server.py

A tiny REAL MCP server (official `mcp` SDK, FastMCP, stdio transport) used
by the MCP tests so they exercise the actual protocol handshake, tool
listing, process lifecycle and crash detection -- not a mock of them.

Flags (one at a time):
  --die-after N   hard-exit the process N seconds after start (simulates a crash)
  --hang          never answer (sleeps forever before serving) -> connect timeout
  --exit-now      exit immediately with status 3 before any handshake
  --many-tools    expose 150 tools instead of 2
"""

import os
import sys
import threading
import time

args = sys.argv[1:]

if "--exit-now" in args:
    sys.exit(3)
if "--hang" in args:
    time.sleep(3600)
if "--die-after" in args:
    delay = float(args[args.index("--die-after") + 1])
    threading.Thread(target=lambda: (time.sleep(delay), os._exit(1)), daemon=True).start()

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("fixture-server")


@mcp.tool()
def echo(text: str) -> str:
    """Echo the text back."""
    return text


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@mcp.tool()
def env_value(name: str) -> str:
    """Return an environment variable (proves env reached the child)."""
    return os.environ.get(name, "")


if "--many-tools" in args:
    for i in range(150):
        def _mk(i=i):
            def tool() -> int:
                return i
            tool.__name__ = f"tool_{i}"
            return tool
        mcp.tool()(_mk())

if __name__ == "__main__":
    mcp.run(transport="stdio")
