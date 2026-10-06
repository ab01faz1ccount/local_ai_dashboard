import { useEffect, useState } from "react";
import { api, type AgentBackendInfo, type AgentSummary, type RuntimeSummary } from "./api";
import { BrowseTab } from "./components/BrowseTab";

type ConfigValue = string | number | boolean | undefined;
type ConfigForm = Record<string, ConfigValue>;

function toStr(v: unknown): string {
  if (v == null) return "";
  if (Array.isArray(v)) return v.join(", ");
  return String(v);
}

/** Strips empty/undefined fields so Save only sends what the user
 * actually set -- the backend merges into the existing config_json, so
 * an omitted field here means "leave whatever it already was alone". */
function cleanConfig(form: ConfigForm): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(form)) {
    if (v === "" || v === undefined) continue;
    out[k] = v;
  }
  return out;
}

const RUNTIME_FIELD_GROUPS: {
  title: string;
  fields: { key: string; label: string; type: "text" | "number" | "checkbox" | "select"; options?: string[] }[];
}[] = [
  {
    title: "Hardware & Memory",
    fields: [
      { key: "n_gpu_layers", label: "GPU Layers (-ngl)", type: "text" },
      { key: "threads", label: "CPU Threads (-t)", type: "number" },
      { key: "ctx_size", label: "Context Size (-c)", type: "number" },
      { key: "cache_type_k", label: "KV Cache Type — K", type: "select", options: ["", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"] },
      { key: "cache_type_v", label: "KV Cache Type — V", type: "select", options: ["", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"] },
      { key: "kv_offload", label: "KV Cache Offload to GPU", type: "checkbox" },
      { key: "device", label: "Device", type: "text" },
      { key: "tensor_split", label: "GPU Split (--tensor-split)", type: "text" },
    ],
  },
  {
    title: "Performance",
    fields: [
      { key: "flash_attn", label: "Flash Attention", type: "checkbox" },
      { key: "batch_size", label: "Batch Size (-b)", type: "number" },
      { key: "ubatch_size", label: "U-Batch Size (-ub)", type: "number" },
      { key: "parallel_slots", label: "Parallel Slots (-np)", type: "number" },
      { key: "numa", label: "NUMA", type: "select", options: ["", "distribute", "isolate", "numactl"] },
      { key: "mlock", label: "mlock (keep model pinned in RAM)", type: "checkbox" },
      { key: "no_mmap", label: "Disable mmap", type: "checkbox" },
    ],
  },
  {
    title: "Sampling & Generation",
    fields: [
      { key: "temperature", label: "Temperature", type: "number" },
      { key: "top_p", label: "Top P", type: "number" },
      { key: "top_k", label: "Top K", type: "number" },
      { key: "min_p", label: "Min P", type: "number" },
      { key: "repeat_penalty", label: "Repeat Penalty", type: "number" },
      { key: "presence_penalty", label: "Presence Penalty", type: "number" },
      { key: "frequency_penalty", label: "Frequency Penalty", type: "number" },
      { key: "seed", label: "Seed", type: "number" },
    ],
  },
  {
    title: "RoPE & Chat Template",
    fields: [
      { key: "rope_scaling", label: "RoPE Scaling", type: "select", options: ["", "none", "linear", "yarn"] },
      { key: "rope_scale", label: "RoPE Scale", type: "number" },
      { key: "rope_freq_base", label: "RoPE Freq Base", type: "number" },
      { key: "rope_freq_scale", label: "RoPE Freq Scale", type: "number" },
      { key: "yarn_orig_ctx", label: "YaRN Orig Context", type: "number" },
      { key: "yarn_ext_factor", label: "YaRN Ext Factor", type: "number" },
      { key: "yarn_attn_factor", label: "YaRN Attn Factor", type: "number" },
      { key: "yarn_beta_fast", label: "YaRN Beta Fast", type: "number" },
      { key: "yarn_beta_slow", label: "YaRN Beta Slow", type: "number" },
      { key: "jinja", label: "Jinja chat template (required for agent tool calling)", type: "checkbox" },
      { key: "chat_template", label: "Chat Template", type: "text" },
    ],
  },
];

const AGENT_FIELD_GROUPS: {
  title: string;
  fields: { key: string; label: string; type: "text" | "number" | "checkbox" | "textarea" }[];
}[] = [
  { title: "Prompt", fields: [{ key: "system_prompt", label: "System / Agent Prompt", type: "textarea" }] },
  {
    title: "Tools",
    fields: [
      { key: "tools", label: "Tools (comma-separated)", type: "text" },
      { key: "allowed_tools", label: "Tool permissions — allowed (comma-separated)", type: "text" },
    ],
  },
  {
    title: "Planning / Loop",
    fields: [{ key: "max_iterations", label: "Max iterations / steps", type: "number" }],
  },
  {
    title: "Memory & Context",
    fields: [
      { key: "memory_enabled", label: "Memory enabled", type: "checkbox" },
      { key: "max_history_messages", label: "Max history messages kept", type: "number" },
    ],
  },
  {
    title: "Timeouts & Safety",
    fields: [
      { key: "timeout_seconds", label: "Tool call timeout (seconds)", type: "number" },
      { key: "require_confirmation", label: "Require confirmation before sensitive actions", type: "checkbox" },
      { key: "concurrency_limit", label: "Concurrent tool calls allowed", type: "number" },
    ],
  },
];

function FieldInput({
  type,
  value,
  onChange,
  options,
}: {
  type: "text" | "number" | "checkbox" | "select" | "textarea";
  value: ConfigValue;
  onChange: (v: ConfigValue) => void;
  options?: string[];
}) {
  if (type === "checkbox") {
    return <input type="checkbox" checked={value === true} onChange={(e) => onChange(e.target.checked)} />;
  }
  if (type === "select") {
    return (
      <select value={toStr(value)} onChange={(e) => onChange(e.target.value)}>
        {(options ?? []).map((o) => (
          <option key={o} value={o}>
            {o || "— Auto / Model Default —"}
          </option>
        ))}
      </select>
    );
  }
  if (type === "textarea") {
    return <textarea value={toStr(value)} onChange={(e) => onChange(e.target.value)} rows={3} style={{ width: "100%" }} />;
  }
  return (
    <input
      type={type === "number" ? "number" : "text"}
      value={toStr(value)}
      onChange={(e) => onChange(type === "number" ? (e.target.value === "" ? "" : Number(e.target.value)) : e.target.value)}
    />
  );
}

/**
 * The panel from the settings guide made real: every llama.cpp launch
 * flag this app knows how to pass through (see
 * core/engine/llama_cpp_engine.py's _build_args), grouped the same way
 * the guide groups them, plus the agent-side behavior knobs. Both save
 * into `config_json` on the runtime/agent -- applied on the next
 * start/restart, not to an already-running process.
 */
export function SettingsPage() {
  const [tab, setTab] = useState<"runtimes" | "agents" | "browse">("runtimes");

  const [runtimes, setRuntimes] = useState<RuntimeSummary[]>([]);
  const [selectedRuntimeId, setSelectedRuntimeId] = useState<number | "">("");
  const [runtimeForm, setRuntimeForm] = useState<ConfigForm>({});
  const [runtimeSaving, setRuntimeSaving] = useState(false);
  const [runtimeSaved, setRuntimeSaved] = useState(false);
  const [runtimeError, setRuntimeError] = useState<string | null>(null);

  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<number | "">("");
  const [agentForm, setAgentForm] = useState<ConfigForm>({});
  const [agentSaving, setAgentSaving] = useState(false);
  const [agentSaved, setAgentSaved] = useState(false);
  const [agentError, setAgentError] = useState<string | null>(null);
  const [agentBackends, setAgentBackends] = useState<AgentBackendInfo[]>([]);
  const [agentBackendDraft, setAgentBackendDraft] = useState("generic");
  const [connectRuntimeId, setConnectRuntimeId] = useState<number | "">("");
  const [connecting, setConnecting] = useState(false);
  const [connectLog, setConnectLog] = useState<string[] | null>(null);

  useEffect(() => {
    api.listRuntimes().then(setRuntimes);
    api.listAgents().then(setAgents);
    api.listAgentBackends().then(setAgentBackends);
  }, []);

  useEffect(() => {
    if (selectedRuntimeId === "") return;
    const rt = runtimes.find((r) => r.id === selectedRuntimeId);
    if (rt) setRuntimeForm(rt.config_json as ConfigForm);
  }, [selectedRuntimeId, runtimes]);

  useEffect(() => {
    if (selectedAgentId === "") return;
    const ag = agents.find((a) => a.id === selectedAgentId);
    if (ag) {
      setAgentForm(ag.config_json as ConfigForm);
      setAgentBackendDraft(ag.agent_backend);
    }
    setConnectLog(null);
  }, [selectedAgentId, agents]);

  /** Something was added from the Browse tab (an agent registered, an
   * install finished, a model added) -- reload what this page shows. */
  function refreshAfterBrowse() {
    api.listRuntimes().then(setRuntimes);
    api.listAgents().then(setAgents);
    api.listAgentBackends().then(setAgentBackends);
  }

  async function saveRuntime() {
    if (selectedRuntimeId === "") return;
    setRuntimeSaving(true);
    setRuntimeSaved(false);
    try {
      const updated = await api.updateRuntimeConfig(selectedRuntimeId, { config_json: cleanConfig(runtimeForm) });
      setRuntimes((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
      setRuntimeSaved(true);
    } finally {
      setRuntimeSaving(false);
    }
  }

  async function saveAgent() {
    if (selectedAgentId === "") return;
    setAgentSaving(true);
    setAgentSaved(false);
    try {
      const updated = await api.updateAgentConfig(selectedAgentId, {
        agent_backend: agentBackendDraft,
        config_json: cleanConfig(agentForm),
      });
      setAgents((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
      setAgentSaved(true);
    } finally {
      setAgentSaving(false);
    }
  }

  async function connectAgent() {
    if (selectedAgentId === "" || connectRuntimeId === "") return;
    setConnecting(true);
    setAgentError(null);
    setConnectLog(null);
    try {
      const result = await api.configureAgent(selectedAgentId, { runtime_id: connectRuntimeId });
      setAgents((prev) => prev.map((a) => (a.id === result.agent.id ? result.agent : a)));
      setConnectLog(result.commands_ran);
    } catch (err) {
      setAgentError(err instanceof Error ? err.message : "Could not connect this agent.");
    } finally {
      setConnecting(false);
    }
  }

  async function deleteSelectedRuntime() {
    if (selectedRuntimeId === "") return;
    const rt = runtimes.find((r) => r.id === selectedRuntimeId);
    if (!rt) return;
    if (
      !window.confirm(
        `«${rt.name}» حذف بشه؟ همه‌ی چت‌های وابسته به این runtime هم برای همیشه پاک می‌شن (نه فقط لینکشون).`
      )
    ) {
      return;
    }
    setRuntimeError(null);
    try {
      await api.deleteRuntime(rt.id);
      setRuntimes((prev) => prev.filter((r) => r.id !== rt.id));
      setSelectedRuntimeId("");
      setRuntimeForm({});
    } catch (err) {
      setRuntimeError(err instanceof Error ? err.message : "Could not delete this runtime.");
    }
  }

  async function deleteSelectedAgent() {
    if (selectedAgentId === "") return;
    const ag = agents.find((a) => a.id === selectedAgentId);
    if (!ag) return;
    if (!window.confirm(`«${ag.name}» حذف بشه؟ چت‌های مرتبط باقی می‌مونن، فقط دیگه ایجنتی بهشون وصل نیست.`)) {
      return;
    }
    setAgentError(null);
    try {
      await api.deleteAgent(ag.id);
      setAgents((prev) => prev.filter((a) => a.id !== ag.id));
      setSelectedAgentId("");
      setAgentForm({});
    } catch (err) {
      setAgentError(err instanceof Error ? err.message : "Could not delete this agent.");
    }
  }

  return (
    <div className="main">
      <section>
        <div className="section-heading">
          <h2>Settings</h2>
          <nav className="sidebar-nav" style={{ flexDirection: "row", gap: 8 }}>
            <button className={tab === "runtimes" ? "active" : ""} onClick={() => setTab("runtimes")}>
              Runtimes (llama.cpp)
            </button>
            <button className={tab === "agents" ? "active" : ""} onClick={() => setTab("agents")}>
              Agents
            </button>
            <button className={tab === "browse" ? "active" : ""} onClick={() => setTab("browse")}>
              Browse
            </button>
          </nav>
        </div>

        {tab === "runtimes" && (
          <>
            <select value={selectedRuntimeId} onChange={(e) => setSelectedRuntimeId(e.target.value === "" ? "" : Number(e.target.value))}>
              <option value="">— pick a runtime —</option>
              {runtimes.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>

            {selectedRuntimeId !== "" && (
              <>
                <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
                  روی start/restart بعدی اعمال می‌شه، نه روی پردازش الان در حال اجرا.
                </p>
                {RUNTIME_FIELD_GROUPS.map((group) => (
                  <div className="panel git-diff-summary" key={group.title}>
                    <div className="section-heading">
                      <h3>{group.title}</h3>
                    </div>
                    <div className="settings-grid">
                      {group.fields.map((f) => (
                        <label className="settings-field" key={f.key}>
                          <span>{f.label}</span>
                          <FieldInput
                            type={f.type}
                            options={f.options}
                            value={runtimeForm[f.key]}
                            onChange={(v) => setRuntimeForm((prev) => ({ ...prev, [f.key]: v }))}
                          />
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
                <button className="primary" disabled={runtimeSaving} onClick={() => void saveRuntime()}>
                  {runtimeSaving ? "Saving…" : "Save"}
                </button>
                <button
                  className="verify-button"
                  style={{ marginInlineStart: 8 }}
                  onClick={() => void deleteSelectedRuntime()}
                >
                  Delete this runtime
                </button>
                {runtimeSaved && <span className="muted" style={{ marginInlineStart: 8 }}>Saved.</span>}
                {runtimeError && <div className="error-text">{runtimeError}</div>}
              </>
            )}
          </>
        )}

        {tab === "agents" && (
          <>
            <select value={selectedAgentId} onChange={(e) => setSelectedAgentId(e.target.value === "" ? "" : Number(e.target.value))}>
              <option value="">— pick an agent —</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>

            {selectedAgentId !== "" && (
              <>
                <div className="panel git-diff-summary">
                  <div className="section-heading">
                    <h3>Backend</h3>
                  </div>
                  <div className="settings-grid">
                    <label className="settings-field">
                      <span>Agent backend</span>
                      <select value={agentBackendDraft} onChange={(e) => setAgentBackendDraft(e.target.value)}>
                        {agentBackends.map((b) => (
                          <option key={b.backend_id} value={b.backend_id}>
                            {b.display_name} {b.backend_id !== "generic" && (b.detected ? "✓ detected" : "— not detected")}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>

                  {agentBackendDraft !== "generic" && (
                    <>
                      <div className="settings-grid" style={{ marginTop: 8 }}>
                        <label className="settings-field">
                          <span>Connect to runtime</span>
                          <select
                            value={connectRuntimeId}
                            onChange={(e) => setConnectRuntimeId(e.target.value === "" ? "" : Number(e.target.value))}
                          >
                            <option value="">— pick a runtime —</option>
                            {runtimes.map((r) => (
                              <option key={r.id} value={r.id}>
                                {r.name}
                              </option>
                            ))}
                          </select>
                        </label>
                      </div>
                      <button
                        className="primary"
                        style={{ marginTop: 8 }}
                        disabled={connectRuntimeId === "" || connecting}
                        onClick={() => void connectAgent()}
                      >
                        {connecting ? "Connecting…" : "Configure & Connect"}
                      </button>
                      {connectLog && (
                        <ul className="compare-eval-list" style={{ marginTop: 8 }}>
                          {connectLog.map((cmd, i) => (
                            <li key={i}>{cmd}</li>
                          ))}
                        </ul>
                      )}
                      <p className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                        برای ترمینال واقعی، از تب Chats چتی که این agent بهش وصله رو باز کن.
                      </p>
                    </>
                  )}
                </div>

                {AGENT_FIELD_GROUPS.map((group) => (
                  <div className="panel git-diff-summary" key={group.title}>
                    <div className="section-heading">
                      <h3>{group.title}</h3>
                    </div>
                    <div className="settings-grid">
                      {group.fields.map((f) => (
                        <label className="settings-field" key={f.key}>
                          <span>{f.label}</span>
                          <FieldInput
                            type={f.type}
                            value={agentForm[f.key]}
                            onChange={(v) => setAgentForm((prev) => ({ ...prev, [f.key]: v }))}
                          />
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
                <button className="primary" disabled={agentSaving} onClick={() => void saveAgent()}>
                  {agentSaving ? "Saving…" : "Save"}
                </button>
                <button
                  className="verify-button"
                  style={{ marginInlineStart: 8 }}
                  onClick={() => void deleteSelectedAgent()}
                >
                  Delete this agent
                </button>
                {agentSaved && <span className="muted" style={{ marginInlineStart: 8 }}>Saved.</span>}
                {agentError && <div className="error-text">{agentError}</div>}
              </>
            )}
          </>
        )}

        {tab === "browse" && <BrowseTab onChanged={refreshAfterBrowse} />}
      </section>
    </div>
  );
}
