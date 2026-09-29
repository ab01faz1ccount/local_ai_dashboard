import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiErrorMessage, connectEventsSocket, type PendingPermissionRequest, type PermissionDecision } from "../api";
import { RISK_LABEL, mergePending, scopeLabel } from "../permission-utils";

/**
 * The live half of the Permission Engine (core/permissions/engine.py):
 * mounted once for the whole app, shows a modal for whatever a caller
 * is currently blocked on `POST /permissions/check` waiting for, and
 * resolves it with one of the four decisions the engine defines.
 *
 * There is no real caller yet -- the future Tool Registry / agent loop
 * is what will actually call `check_or_request` before running a tool
 * -- but the request/response plumbing (this component, plus the
 * backend it talks to) is real and exercised by its own tests, so the
 * day a caller exists, approving a request already works.
 *
 * Two sources feed the queue: an initial GET /pending (catches a
 * request that started before this mounted, e.g. a page reload), and
 * `permission.requested`/`permission.approved`/`permission.denied` on
 * the existing events websocket (LogsPage's `connectEventsSocket`) --
 * no new transport, since a request/response over the event log is
 * exactly what that socket already carries.
 */
export function PermissionPrompt() {
  const [queue, setQueue] = useState<PendingPermissionRequest[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const queueRef = useRef(queue);
  queueRef.current = queue;

  const refresh = useCallback(() => {
    api
      .listPendingPermissions()
      .then((snapshot) => setQueue((q) => mergePending(q, snapshot)))
      .catch(() => undefined); // transient -- the next poll/event will catch up
  }, []);

  useEffect(() => {
    refresh();
    const cleanup = connectEventsSocket((ev) => {
      if (ev.event_type === "permission.requested") {
        refresh();
      } else if (ev.event_type === "permission.approved" || ev.event_type === "permission.denied") {
        const id = ev.metadata?.request_id;
        if (typeof id === "string") setQueue((q) => q.filter((r) => r.id !== id));
      }
    });
    return cleanup;
  }, [refresh]);

  const current = queue[0];

  async function decide(decision: PermissionDecision) {
    if (!current) return;
    setBusy(true);
    setError(null);
    try {
      await api.resolvePermissionRequest(current.id, decision);
      setQueue((q) => q.filter((r) => r.id !== current.id));
    } catch (err) {
      setError(apiErrorMessage(err, "Could not send that decision."));
    } finally {
      setBusy(false);
    }
  }

  if (!current) return null;

  return (
    <div className="permission-overlay" role="alertdialog" aria-modal="true" aria-label="Permission request">
      <div className="panel permission-modal">
        <div className={`permission-risk permission-risk-${current.risk_level.toLowerCase()}`}>{RISK_LABEL[current.risk_level]}</div>
        <h3 className="permission-title">{scopeLabel(current.scope_type, current.scope_key)} wants to run</h3>
        {current.description && <p className="muted">{current.description}</p>}
        <p className="muted mono permission-scope">{current.scope_type}: {current.scope_key}</p>
        {queue.length > 1 && <p className="muted">+{queue.length - 1} more waiting</p>}

        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}

        <div className="permission-actions">
          <button className="primary" disabled={busy} onClick={() => void decide("allow_once")}>
            Allow once
          </button>
          <button disabled={busy} onClick={() => void decide("allow_session")}>
            Allow for session
          </button>
          <button disabled={busy} onClick={() => void decide("allow_always")}>
            Always allow
          </button>
          <button disabled={busy} onClick={() => void decide("deny")}>
            Deny
          </button>
        </div>
      </div>
    </div>
  );
}
