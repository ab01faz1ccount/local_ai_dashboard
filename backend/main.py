"""
backend/main.py

App entrypoint. Wires together: DB init, the REST router (token-protected,
except the one bootstrap-token route below), the WebSocket router, and
the two remaining "day-0, non-negotiable" security requirements that live
at the app/process level rather than inside a single route:

  1. Origin allowlisting -- via Starlette's real CORSMiddleware, not a
     hand-rolled header check. A hand-rolled check can't answer a
     browser's CORS preflight (OPTIONS) request correctly, which is
     exactly the bug this replaced: the preflight got no
     Access-Control-Allow-Origin header back, so the browser blocked
     every request before it even reached our token check.
  2. Binding to 127.0.0.1 only -- enforced in the `if __name__` block
     below, unconditionally. Remote Mode is Future Roadmap and is not
     wired to anything yet, so LAI_REMOTE_MODE is only ever logged as a
     warning, never actually honored.
"""

from __future__ import annotations

import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.discovery import router as discovery_router
from .api.http import require_token
from .api.http import router as http_router
from .api.mcp import router as mcp_router
from .api.permissions import router as permissions_router
from .api.ws import router as ws_router
from .core.mcp import mcp_manager
from .core.security import get_allowed_origins, get_or_create_access_token
from .storage import db as storage_db
from .storage.db import init_db

app = FastAPI(title="Local AI Control Center", version="0.1.0")

# CORSMiddleware handles the browser preflight (OPTIONS) automatically and
# only attaches Access-Control-Allow-Origin for origins in the allowlist --
# a request from anywhere else still gets a response from the server, but
# the browser refuses to hand it to the page's JS, which is the actual
# protection against a malicious webpage abusing the local API. Non-browser
# clients (curl, Synapse, etc.) aren't subject to CORS at all -- they still
# go through require_token below regardless of Origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(get_allowed_origins()),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.on_event("startup")
def _on_startup() -> None:
    init_db()
    # Nothing is live at process start, so a CONNECTED/CONNECTING MCP row
    # left over from the previous run is stale -- reset it.
    with storage_db.SessionLocal() as db:
        mcp_manager.reset_stale_statuses(db)


@app.on_event("shutdown")
def _on_shutdown() -> None:
    # Terminates any stdio MCP server child processes instead of orphaning them.
    mcp_manager.shutdown()


@app.get("/api/v1/auth/bootstrap-token")
def bootstrap_token() -> dict:
    """Deliberately outside `require_token` -- this is the one route that
    can't require the token, since its whole job is handing it out.

    This doesn't lower the actual security bar: the token already lives
    in a plaintext file (`local_config.json`) on the same machine, so any
    local process can already read it directly. The thing that matters
    here -- stopping a malicious *webpage* from silently pulling the
    token through the user's browser -- is still enforced the normal way:
    CORSMiddleware above only ever attaches Access-Control-Allow-Origin
    for origins in the allowlist, so the browser withholds this response
    from any page not running as the app's own local frontend, exactly
    like every other route. Lets the frontend pair itself automatically
    on first load instead of making the user copy/paste the token by
    hand -- see App.tsx."""
    return {"access_token": get_or_create_access_token()}


# Every REST route requires the local access token. discovery_router (the
# Settings -> Browse endpoints: local file picker, Hugging Face model
# search/download, GitHub agent search/install) already declares its own
# `dependencies=[Depends(require_token)]` at the router level (see
# api/discovery.py), same protection either way -- listed separately here
# only because it's mounted on its own prefix, not because it's exempt.
# The WebSocket router checks its own token via query param (see api/ws.py)
# since WS handshakes can't carry a custom Authorization header from a
# browser, and WS connections aren't part of the CORS preflight flow above
# either.
app.include_router(http_router, dependencies=[Depends(require_token)])
app.include_router(discovery_router)
app.include_router(mcp_router)
app.include_router(permissions_router)
app.include_router(ws_router)


if __name__ == "__main__":
    import uvicorn

    if os.environ.get("LAI_REMOTE_MODE"):
        print(
            "[warning] LAI_REMOTE_MODE is set, but Remote Mode is Future "
            "Roadmap and not implemented. Still binding to 127.0.0.1 only."
        )

    uvicorn.run(app, host="127.0.0.1", port=8420)
