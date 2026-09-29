import { useCallback, useEffect, useState } from "react";
import { api, apiErrorMessage, type PermissionGrant, type PermissionScopeType } from "./api";
import { formatFullTimestamp } from "./format";
import { RISK_LABEL, scopeLabel } from "./permission-utils";

/**
 * Standing permission grants (core/permissions/engine.py): what's been
 * allowed for a session or forever, so it can be reviewed and revoked
 * without waiting to be asked again. Live requests are handled by
 * PermissionPrompt (mounted globally); this page is the audit/management
 * side, same relationship LogsPage has to the event system.
 */
export function PermissionsPage() {
  const [grants, setGrants] = useState<PermissionGrant[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeOnly, setActiveOnly] = useState(true);
  const [scopeType, setScopeType] = useState<PermissionScopeType | "">("");
  const [revoking, setRevoking] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setGrants(await api.listPermissionGrants({ activeOnly, scopeType: scopeType || undefined }));
    } catch (err) {
      setError(apiErrorMessage(err, "Could not load permissions."));
    } finally {
      setLoading(false);
    }
  }, [activeOnly, scopeType]);

  useEffect(() => {
    void load();
  }, [load]);

  async function revoke(id: number) {
    setRevoking(id);
    try {
      await api.revokePermissionGrant(id);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err, "Could not revoke that grant."));
    } finally {
      setRevoking(null);
    }
  }

  return (
    <div className="main">
      <div className="section-heading">
        <h2>Permissions</h2>
      </div>
      <p className="muted mcp-subtitle">
        Standing decisions tools were granted — for a session, or always. One-time allow/deny decisions aren't
        listed here since they don't apply again.
      </p>

      <div className="logs-toolbar">
        <label className="mcp-inline-check">
          <input type="checkbox" checked={activeOnly} onChange={(e) => setActiveOnly(e.target.checked)} />
          Active only
        </label>
        <select value={scopeType} onChange={(e) => setScopeType(e.target.value as PermissionScopeType | "")}>
          <option value="">All scopes</option>
          <option value="mcp_server">MCP servers</option>
          <option value="mcp_tool">MCP tools</option>
        </select>
      </div>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {loading ? (
        <p className="muted">Loading…</p>
      ) : grants.length === 0 ? (
        <p className="muted">No {activeOnly ? "active " : ""}permission grants.</p>
      ) : (
        <div className="panel permission-grants-table">
          {grants.map((g) => (
            <div key={g.id} className="permission-grant-row" data-testid={`grant-${g.id}`}>
              <div>
                <div>{scopeLabel(g.scope_type, g.scope_key)}</div>
                <div className="muted mono">{g.scope_type}: {g.scope_key}</div>
              </div>
              <span className={`permission-risk permission-risk-${g.risk_level.toLowerCase()}`}>{RISK_LABEL[g.risk_level]}</span>
              <span className="mcp-tag">{g.decision}</span>
              <span className="muted">{formatFullTimestamp(g.granted_at)}</span>
              <span className={g.active ? "muted" : "muted"}>{g.active ? "Active" : "Ended"}</span>
              {g.active ? (
                <button disabled={revoking === g.id} onClick={() => void revoke(g.id)}>
                  {revoking === g.id ? "Revoking…" : "Revoke"}
                </button>
              ) : (
                <span />
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
