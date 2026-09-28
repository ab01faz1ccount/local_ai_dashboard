"""End-to-end check of the real backend/main.py: startup wiring, CORS/
token middleware, and that discovery_router is actually mounted and
shares the same DB/token as everything else -- as opposed to
test_router.py, which builds its own minimal FastAPI app from the routers
directly and so wouldn't catch a mistake in main.py itself (e.g.
forgetting to call app.include_router(discovery_router))."""

import json

from fastapi.testclient import TestClient

from backend.core import events
from backend.main import app


def test_bootstrap_then_both_routers_share_the_token_and_db():
    with TestClient(app) as client:
        token = client.get("/api/v1/auth/bootstrap-token").json()["access_token"]
        auth = {"Authorization": f"Bearer {token}"}

        # http.py's routes
        assert client.get("/api/v1/agents", headers=auth).status_code == 200
        assert client.get("/api/v1/agents").status_code == 401

        # discovery.py's routes, mounted from main.py
        assert client.get("/api/v1/discover/fs/list", headers=auth).status_code == 200
        assert client.get("/api/v1/discover/fs/list").status_code == 401
        assert client.get("/api/v1/discover/models/dir", headers=auth).status_code == 200

        # something registered through discovery.py is visible through http.py's own routes -> same DB
        client.post(
            "/api/v1/discover/agents/register-local",
            headers=auth,
            json={"path": __file__},  # deliberately not an agent; just proves the route round-trips without a 404/import error
        )
        assert client.get("/api/v1/agent-backends", headers=auth).status_code == 200

        # core/events/ routes and websocket, mounted (as part of http_router
        # / ws_router respectively) from the same main.py
        assert client.get("/api/v1/events", headers=auth).status_code == 200
        assert client.get("/api/v1/events").status_code == 401
        assert client.get("/api/v1/events/types", headers=auth).status_code == 200

        with client.websocket_connect(f"/ws/events?token={token}") as socket:
            events.emit(events.EventType.RUNTIME_STARTED, metadata={"via": "main.py wiring test"})
            payload = json.loads(socket.receive_text())
        assert payload["event_type"] == "runtime.started"
        # ...and that same event is independently visible via the REST history.
        history_types = [e["event_type"] for e in client.get("/api/v1/events", headers=auth).json()]
        assert "runtime.started" in history_types
