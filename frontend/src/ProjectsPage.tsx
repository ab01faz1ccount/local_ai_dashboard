import { useEffect, useState } from "react";
import { api, type ChatSummary, type ProjectSummary } from "./api";
import { GitPanel } from "./components/GitPanel";
import { ProjectUsagePanel } from "./components/ProjectUsagePanel";

/**
 * Projects: the local-first "workspace" concept Jan-style tools use to
 * group a history of chats under one folder/repo. A chat never has to
 * start inside a project -- any chat can be "promoted" into one (new or
 * existing) later, from the unassigned list below. Each project then
 * gets its own Chats + Git tabs.
 */
export function ProjectsPage({ onOpenChat }: { onOpenChat: (chatId: number) => void }) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [unassignedChats, setUnassignedChats] = useState<ChatSummary[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [tab, setTab] = useState<"chats" | "git" | "usage">("chats");
  const [projectChats, setProjectChats] = useState<ChatSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [promotingChatId, setPromotingChatId] = useState<number | null>(null);
  const [promoteMode, setPromoteMode] = useState<"existing" | "new">("new");
  const [promoteExistingId, setPromoteExistingId] = useState("");
  const [promoteName, setPromoteName] = useState("");
  const [promotePath, setPromotePath] = useState("");

  async function refreshAll() {
    try {
      const [p, u] = await Promise.all([api.listProjects(), api.listChats({ unassigned: true })]);
      setProjects(p);
      setUnassignedChats(u);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load projects.");
    }
  }

  useEffect(() => {
    void refreshAll();
  }, []);

  useEffect(() => {
    if (!selectedProjectId) {
      setProjectChats([]);
      return;
    }
    api.listChats({ projectId: selectedProjectId }).then(setProjectChats).catch(() => setProjectChats([]));
  }, [selectedProjectId]);

  const selectedProject = projects.find((p) => p.id === selectedProjectId) ?? null;

  function startPromoting(chatId: number) {
    setPromotingChatId(chatId);
    setPromoteMode(projects.length > 0 ? "existing" : "new");
    setPromoteExistingId(projects[0]?.id ?? "");
    setPromoteName("");
    setPromotePath("");
  }

  async function confirmPromote() {
    if (promotingChatId == null) return;
    try {
      const body =
        promoteMode === "existing"
          ? { project_id: promoteExistingId }
          : { new_project_name: promoteName.trim(), local_path: promotePath.trim() || undefined };
      if (promoteMode === "new" && !promoteName.trim()) return;
      await api.promoteChat(promotingChatId, body);
      setPromotingChatId(null);
      await refreshAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not promote this chat.");
    }
  }

  return (
    <div className="main">
      {error && <div className="error-text">{error}</div>}

      <section>
        <div className="section-heading">
          <h2>Projects</h2>
        </div>
        <div className="projects-grid">
          {projects.map((p) => (
            <button
              key={p.id}
              className={`project-card ${selectedProjectId === p.id ? "active" : ""}`}
              onClick={() => {
                setSelectedProjectId(p.id);
                setTab("chats");
              }}
            >
              <div>{p.name}</div>
              {p.local_path && <div className="muted mono">{p.local_path}</div>}
            </button>
          ))}
          {projects.length === 0 && <p className="muted">هنوز پروژه‌ای نساختی — یه چت رو از پایین به پروژه تبدیل کن.</p>}
        </div>
      </section>

      {unassignedChats.length > 0 && (
        <section>
          <div className="section-heading">
            <h2>Unassigned chats</h2>
          </div>
          <table className="models-table">
            <tbody>
              {unassignedChats.map((c) => (
                <tr key={c.id}>
                  <td>
                    <button className="link-button" onClick={() => onOpenChat(c.id)}>
                      {c.title}
                    </button>
                  </td>
                  <td className="muted mono">{new Date(c.updated_at).toLocaleString()}</td>
                  <td>
                    {promotingChatId === c.id ? (
                      <div className="hf-link-editor">
                        {projects.length > 0 && (
                          <select value={promoteMode} onChange={(e) => setPromoteMode(e.target.value as "existing" | "new")}>
                            <option value="existing">پروژه‌ی موجود</option>
                            <option value="new">پروژه‌ی جدید</option>
                          </select>
                        )}
                        {promoteMode === "existing" ? (
                          <select value={promoteExistingId} onChange={(e) => setPromoteExistingId(e.target.value)}>
                            {projects.map((p) => (
                              <option key={p.id} value={p.id}>
                                {p.name}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <>
                            <input
                              placeholder="نام پروژه"
                              value={promoteName}
                              onChange={(e) => setPromoteName(e.target.value)}
                            />
                            <input
                              placeholder="/path/to/project (اختیاری)"
                              value={promotePath}
                              onChange={(e) => setPromotePath(e.target.value)}
                            />
                          </>
                        )}
                        <button className="primary" onClick={() => void confirmPromote()}>
                          Save
                        </button>
                        <button onClick={() => setPromotingChatId(null)}>Cancel</button>
                      </div>
                    ) : (
                      <button className="link-button" onClick={() => startPromoting(c.id)}>
                        تبدیل به پروژه
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {selectedProject && (
        <section>
          <div className="section-heading">
            <h2>{selectedProject.name}</h2>
            <nav className="sidebar-nav" style={{ flexDirection: "row", gap: 8 }}>
              <button className={tab === "chats" ? "active" : ""} onClick={() => setTab("chats")}>
                Chats
              </button>
              <button className={tab === "git" ? "active" : ""} onClick={() => setTab("git")}>
                Git
              </button>
              <button className={tab === "usage" ? "active" : ""} onClick={() => setTab("usage")}>
                Usage
              </button>
            </nav>
          </div>

          {tab === "chats" && (
            <table className="models-table">
              <tbody>
                {projectChats.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <button className="link-button" onClick={() => onOpenChat(c.id)}>
                        {c.title}
                      </button>
                    </td>
                    <td className="muted mono">{new Date(c.updated_at).toLocaleString()}</td>
                  </tr>
                ))}
                {projectChats.length === 0 && (
                  <tr>
                    <td className="muted">هیچ چتی هنوز به این پروژه وصل نشده.</td>
                  </tr>
                )}
              </tbody>
            </table>
          )}

          {tab === "git" && (
            <GitPanel
              project={selectedProject}
              onProjectUpdated={(p) => setProjects((prev) => prev.map((x) => (x.id === p.id ? p : x)))}
            />
          )}

          {tab === "usage" && <ProjectUsagePanel projectId={selectedProject.id} />}
        </section>
      )}
    </div>
  );
}
