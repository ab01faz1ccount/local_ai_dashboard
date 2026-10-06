import { useEffect, useState } from "react";
import { connectEventsSocket } from "../api";
import { addNotification, eventToNotification, type AppNotification } from "../notification-utils";
import { formatClockTime } from "../format";

/**
 * Notifications (master build prompt section 24): a bell in the app
 * chrome that collects the few events worth interrupting someone for --
 * a runtime crash, a permission request waiting, a lost MCP connection --
 * from the same /ws/events socket everything else already uses. What
 * counts as notification-worthy lives in notification-utils.ts; this
 * component only holds the list, the unread count, and the dropdown.
 */
export function NotificationsPanel() {
  // One state object, not two: the unread count must change in the same
  // atomic update as the list (a setState call inside another setState's
  // updater function runs twice under StrictMode and double-counts).
  const [state, setState] = useState<{ items: AppNotification[]; unread: number }>({ items: [], unread: 0 });
  const [open, setOpen] = useState(false);
  const { items, unread } = state;

  useEffect(() => {
    return connectEventsSocket((ev) => {
      const n = eventToNotification(ev);
      if (n == null) return;
      setState((prev) => {
        const next = addNotification(prev.items, n);
        return next === prev.items ? prev : { items: next, unread: prev.unread + 1 };
      });
    });
  }, []);

  function toggle() {
    setOpen((o) => !o);
    setState((prev) => ({ ...prev, unread: 0 }));
  }

  return (
    <div className="notifications">
      <button className="notifications-bell" onClick={toggle} aria-label="Notifications" aria-expanded={open}>
        🔔{unread > 0 && <span className="notifications-badge" aria-label={`${unread} unread`}>{unread}</span>}
      </button>
      {open && (
        <div className="panel notifications-dropdown" role="region" aria-label="Notifications list">
          {items.length === 0 ? (
            <div className="muted">Nothing needs your attention.</div>
          ) : (
            <>
              {items.map((n) => (
                <div key={n.id} className={`notification notification-${n.severity}`} data-testid="notification">
                  <div className="notification-title">{n.title}</div>
                  {n.detail && <div className="muted notification-detail">{n.detail}</div>}
                  <div className="muted mono notification-time">{formatClockTime(n.timestamp)}</div>
                </div>
              ))}
              <button
                onClick={() => setState({ items: [], unread: 0 })}
              >
                Clear all
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
