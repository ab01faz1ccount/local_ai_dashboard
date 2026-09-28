import { useEffect, useState } from "react";
import { ApiError, api, getStoredToken, setStoredToken } from "./api";
import { ChatView } from "./ChatView";
import { Dashboard } from "./Dashboard";
import { LogsPage } from "./LogsPage";
import { ModelsPage } from "./ModelsPage";
import { OnboardingWizard } from "./OnboardingWizard";
import { ProjectsPage } from "./ProjectsPage";
import { SettingsPage } from "./SettingsPage";

/**
 * Top-level app shell.
 *
 * Two things used to gate the entire app before the user saw anything:
 * a manual "paste your token" screen, and a forced multi-step wizard
 * that had to be finished before the dashboard was reachable at all.
 * Both are gone as blocking gates now, following the pattern local-model
 * tools like Jan converge on: land on the dashboard immediately, and
 * make setup a normal thing reachable from the nav -- not a prerequisite
 * for seeing the app.
 *
 *   1. Local pairing -- tries to fetch the access token automatically
 *      (see `api.bootstrapToken`) since the frontend and backend run on
 *      the same machine. Only falls back to the manual AuthGate if that
 *      fails (wrong origin, backend not running yet, etc.).
 *   2. MainShell mounts as soon as a token is confirmed valid. Setup
 *      (llama.cpp detection, first model/runtime/agent -- the old
 *      wizard) is just another nav item now; Dashboard shows its own
 *      empty-state prompt into it when there's nothing configured yet.
 */
export default function App() {
  const [token, setTokenState] = useState<string | null>(getStoredToken());
  const [bootstrapping, setBootstrapping] = useState(token == null);
  const [ready, setReady] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);

  useEffect(() => {
    if (token) return;
    (async () => {
      try {
        const { access_token } = await api.bootstrapToken();
        setStoredToken(access_token);
        setTokenState(access_token);
      } catch {
        // Backend unreachable, or this frontend isn't running on an
        // origin the backend's CORS allowlist trusts -- either way, the
        // manual AuthGate below is the fallback.
      } finally {
        setBootstrapping(false);
      }
    })();
  }, [token]);

  useEffect(() => {
    if (!token) return;
    setReady(false);
    setCheckError(null);
    (async () => {
      try {
        await api.getOnboardingState(); // cheap reachability + token check
        setReady(true);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          setTokenState(null);
        } else {
          setCheckError(err instanceof Error ? err.message : "Could not reach the backend.");
        }
      }
    })();
  }, [token]);

  if (bootstrapping) {
    return null; // brief loading state, avoids a flash of the auth gate on the normal path
  }

  if (!token) {
    return <AuthGate onConnected={setTokenState} />;
  }

  if (checkError) {
    return (
      <div className="auth-gate">
        <div className="panel">
          <p className="error-text">{checkError}</p>
          <p className="muted">
            Is the backend running? Start it with <span className="mono">python -m backend.main</span>.
          </p>
        </div>
      </div>
    );
  }

  if (!ready) {
    return null; // brief loading state, avoids a flash of the wrong screen
  }

  return <MainShell />;
}

type View = "dashboard" | "models" | "projects" | "chats" | "logs" | "setup" | "settings";

function MainShell() {
  const [view, setView] = useState<View>("dashboard");
  const [pendingChatId, setPendingChatId] = useState<number | null>(null);

  function openChat(chatId: number) {
    setPendingChatId(chatId);
    setView("chats");
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">Local AI Control Center</div>
        <nav className="sidebar-nav">
          <button className={view === "dashboard" ? "active" : ""} onClick={() => setView("dashboard")}>
            Dashboard
          </button>
          <button className={view === "models" ? "active" : ""} onClick={() => setView("models")}>
            Models
          </button>
          <button className={view === "projects" ? "active" : ""} onClick={() => setView("projects")}>
            Projects
          </button>
          <button className={view === "chats" ? "active" : ""} onClick={() => setView("chats")}>
            Chats
          </button>
          <button className={view === "logs" ? "active" : ""} onClick={() => setView("logs")}>
            Logs
          </button>
          <button className={view === "setup" ? "active" : ""} onClick={() => setView("setup")}>
            Setup
          </button>
          <button className={view === "settings" ? "active" : ""} onClick={() => setView("settings")}>
            Settings
          </button>
        </nav>
      </aside>

      {view === "dashboard" && <Dashboard onAddRuntime={() => setView("setup")} />}
      {view === "models" && <ModelsPage />}
      {view === "projects" && <ProjectsPage onOpenChat={openChat} />}
      {view === "chats" && <ChatView initialChatId={pendingChatId} />}
      {view === "logs" && <LogsPage />}
      {view === "setup" && (
        <OnboardingWizard onComplete={() => setView("dashboard")} onCancel={() => setView("dashboard")} />
      )}
      {view === "settings" && <SettingsPage />}
    </div>
  );
}

function AuthGate({ onConnected }: { onConnected: (token: string) => void }) {
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  async function handleConnect() {
    setChecking(true);
    setError(null);
    setStoredToken(input.trim());
    try {
      await api.getOnboardingState();
      onConnected(input.trim());
    } catch (err) {
      setError(
        err instanceof ApiError
          ? "That token was rejected. Check local_config.json in the project folder."
          : "Could not reach the backend. Is it running on 127.0.0.1:8420?"
      );
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="auth-gate">
      <div className="panel">
        <h2 style={{ margin: 0 }}>Connect to your Control Center</h2>
        <p className="muted">
          Couldn't pair automatically. Paste the access token from{" "}
          <span className="mono">local_config.json</span> in your project folder.
        </p>
        <input
          placeholder="access token"
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
        {error && <p className="error-text">{error}</p>}
        <button className="primary" disabled={!input || checking} onClick={handleConnect}>
          {checking ? "Connecting…" : "Connect"}
        </button>
      </div>
    </div>
  );
}
