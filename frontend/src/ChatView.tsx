import { useEffect, useRef, useState } from "react";
import {
  api,
  type AgentBackendInfo,
  type AgentSummary,
  type ChatMessageItem,
  type ChatSummary,
  type GlobalSearchResult,
  type RuntimeSummary,
} from "./api";
import { AgentTerminal } from "./components/AgentTerminal";

/**
 * The chat surface: every LLM gets one, whether or not it has an agent
 * attached (a chat's agent_id is nullable). Left column is the chat list
 * plus a global search box; right column is the active thread with its
 * own in-chat search toggle.
 *
 * `initialChatId`, when given, is a chat to jump straight to on load --
 * used when this view is opened by clicking a chat from the Projects
 * page rather than from its own chat list.
 */
export function ChatView({ initialChatId }: { initialChatId?: number | null } = {}) {
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [runtimes, setRuntimes] = useState<RuntimeSummary[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [agentBackends, setAgentBackends] = useState<AgentBackendInfo[]>([]);
  const [selectedChatId, setSelectedChatId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessageItem[]>([]);
  const [viewMode, setViewMode] = useState<"chat" | "terminal">("chat");

  const [showNewChatForm, setShowNewChatForm] = useState(false);
  const [newChatRuntimeId, setNewChatRuntimeId] = useState<number | "">("");
  const [newChatAgentId, setNewChatAgentId] = useState<number | "">("");

  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [globalQuery, setGlobalQuery] = useState("");
  const [globalResults, setGlobalResults] = useState<GlobalSearchResult[] | null>(null);

  const [inChatQuery, setInChatQuery] = useState("");
  const [inChatResults, setInChatResults] = useState<ChatMessageItem[] | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    (async () => {
      const [chatList, runtimeList, agentList, backendList] = await Promise.all([
        api.listChats(),
        api.listRuntimes(),
        api.listAgents(),
        api.listAgentBackends(),
      ]);
      setChats(chatList);
      setRuntimes(runtimeList);
      setAgents(agentList);
      setAgentBackends(backendList);
      if (initialChatId != null && chatList.some((c) => c.id === initialChatId)) {
        setSelectedChatId(initialChatId);
      } else if (chatList.length > 0) {
        setSelectedChatId(chatList[0].id);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialChatId]);

  useEffect(() => {
    if (selectedChatId == null) {
      setMessages([]);
      return;
    }
    api.getChatMessages(selectedChatId).then(setMessages);
    setInChatResults(null);
    setInChatQuery("");
  }, [selectedChatId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  function runtimeName(id: number) {
    return runtimes.find((r) => r.id === id)?.name ?? `runtime #${id}`;
  }

  async function handleCreateChat() {
    if (newChatRuntimeId === "") return;
    const chat = await api.createChat({
      runtime_id: newChatRuntimeId,
      agent_id: newChatAgentId === "" ? null : newChatAgentId,
    });
    setChats((prev) => [chat, ...prev]);
    setSelectedChatId(chat.id);
    setShowNewChatForm(false);
    setNewChatRuntimeId("");
    setNewChatAgentId("");
  }

  async function handleDeleteChat(id: number) {
    await api.deleteChat(id);
    setChats((prev) => prev.filter((c) => c.id !== id));
    if (selectedChatId === id) setSelectedChatId(null);
  }

  async function handleSend() {
    if (!draft.trim() || selectedChatId == null) return;
    setSending(true);
    setError(null);
    const content = draft;
    setDraft("");
    try {
      await api.sendChatMessage(selectedChatId, content);
      const [updated, chatList] = await Promise.all([api.getChatMessages(selectedChatId), api.listChats()]);
      setMessages(updated);
      setChats(chatList);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send the message.");
      setDraft(content); // give the message back so it isn't lost
    } finally {
      setSending(false);
    }
  }

  async function handleGlobalSearch() {
    if (!globalQuery.trim()) {
      setGlobalResults(null);
      return;
    }
    setGlobalResults(await api.globalSearch(globalQuery));
  }

  async function handleInChatSearch() {
    if (!inChatQuery.trim() || selectedChatId == null) {
      setInChatResults(null);
      return;
    }
    setInChatResults(await api.searchInChat(selectedChatId, inChatQuery));
  }

  function openResult(chatId: number) {
    setGlobalResults(null);
    setGlobalQuery("");
    setSelectedChatId(chatId);
  }

  const selectedChat = chats.find((c) => c.id === selectedChatId) ?? null;
  const selectedAgent = selectedChat?.agent_id != null ? agents.find((a) => a.id === selectedChat.agent_id) ?? null : null;
  const hasRealBackend = selectedAgent != null && selectedAgent.agent_backend !== "generic";
  const backendInfo = selectedAgent != null ? agentBackends.find((b) => b.backend_id === selectedAgent.agent_backend) ?? null : null;

  useEffect(() => {
    setViewMode(hasRealBackend ? "terminal" : "chat");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedChatId]);

  return (
    <div className="main">
      <div className="chat-shell">
        <aside className="chat-sidebar">
          <input
            placeholder="Search all chats…"
            value={globalQuery}
            onChange={(e) => setGlobalQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleGlobalSearch()}
          />

          {globalResults != null ? (
            <div className="search-results-list">
              {globalResults.length === 0 && <div className="empty-state">No matches.</div>}
              {globalResults.map((r) => (
                <div className="search-result-item" key={r.message_id} onClick={() => openResult(r.chat_id)}>
                  <div className="search-result-meta">
                    {r.chat_title} · {r.role}
                  </div>
                  {r.content.slice(0, 120)}
                </div>
              ))}
              <button onClick={() => setGlobalResults(null)}>Back to chat list</button>
            </div>
          ) : (
            <>
              <button className="primary" onClick={() => setShowNewChatForm((v) => !v)}>
                + New chat
              </button>

              {showNewChatForm && (
                <div className="new-chat-form">
                  <select value={newChatRuntimeId} onChange={(e) => setNewChatRuntimeId(Number(e.target.value))}>
                    <option value="">— pick an LLM —</option>
                    {runtimes.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.name}
                      </option>
                    ))}
                  </select>
                  <select
                    value={newChatAgentId}
                    onChange={(e) => setNewChatAgentId(e.target.value ? Number(e.target.value) : "")}
                  >
                    <option value="">no agent — chat directly</option>
                    {agents.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                  <button className="primary" disabled={newChatRuntimeId === ""} onClick={handleCreateChat}>
                    Start
                  </button>
                </div>
              )}

              <div className="chat-list">
                {chats.map((c) => (
                  <div
                    key={c.id}
                    className={`chat-list-item ${c.id === selectedChatId ? "active" : ""}`}
                    onClick={() => setSelectedChatId(c.id)}
                  >
                    <div className="chat-list-item-title">{c.title}</div>
                    <div className="chat-list-item-meta">{runtimeName(c.runtime_id)}</div>
                  </div>
                ))}
                {chats.length === 0 && <div className="empty-state">No chats yet.</div>}
              </div>
            </>
          )}
        </aside>

        <section className="panel chat-main" style={{ padding: 16 }}>
          {selectedChat == null ? (
            <div className="empty-state">Pick a chat, or start a new one.</div>
          ) : (
            <>
              <div className="chat-header">
                <div>
                  <div className="runtime-name">{selectedChat.title}</div>
                  <div className="runtime-model">{runtimeName(selectedChat.runtime_id)}</div>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  {hasRealBackend && (
                    <div className="sidebar-nav" style={{ flexDirection: "row", gap: 4 }}>
                      <button className={viewMode === "chat" ? "active" : ""} onClick={() => setViewMode("chat")}>
                        Chat
                      </button>
                      <button
                        className={viewMode === "terminal" ? "active" : ""}
                        onClick={() => setViewMode("terminal")}
                      >
                        Terminal
                      </button>
                    </div>
                  )}
                  <input
                    placeholder="Search this chat…"
                    value={inChatQuery}
                    onChange={(e) => setInChatQuery(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleInChatSearch()}
                  />
                  <button onClick={() => handleDeleteChat(selectedChat.id)}>Delete</button>
                </div>
              </div>

              {viewMode === "terminal" && hasRealBackend && selectedAgent ? (
                <AgentTerminal
                  agentId={selectedAgent.id}
                  runtimeId={selectedChat.runtime_id}
                  brandColor={backendInfo?.brand_color ?? "#888888"}
                />
              ) : inChatResults != null ? (
                <div className="search-results-list">
                  {inChatResults.length === 0 && <div className="empty-state">No matches in this chat.</div>}
                  {inChatResults.map((m) => (
                    <div className="search-result-item" key={m.id}>
                      <div className="search-result-meta">{m.role}</div>
                      {m.content.slice(0, 200)}
                    </div>
                  ))}
                  <button
                    onClick={() => {
                      setInChatResults(null);
                      setInChatQuery("");
                    }}
                  >
                    Back to conversation
                  </button>
                </div>
              ) : (
                <>
                  <div className="chat-messages">
                    {messages.map((m) => (
                      <div key={m.id} className={`chat-bubble ${m.role}`}>
                        {m.content}
                        {m.role === "assistant" && m.latency_ms != null && (
                          <div className="chat-bubble-meta">{Math.round(m.latency_ms)} ms</div>
                        )}
                      </div>
                    ))}
                    <div ref={messagesEndRef} />
                  </div>

                  {error && <div className="error-text">{error}</div>}

                  <div className="chat-input-bar">
                    <textarea
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          handleSend();
                        }
                      }}
                      placeholder="Message this LLM…"
                    />
                    <button className="primary" disabled={sending || !draft.trim()} onClick={handleSend}>
                      {sending ? "Sending…" : "Send"}
                    </button>
                  </div>
                </>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
