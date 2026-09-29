/**
 * frontend/src/api.ts
 *
 * Typed REST + WebSocket client for the backend. Every REST call attaches
 * the local access token as a Bearer header; the WebSocket attaches it as
 * a query param instead, since browsers can't set custom headers on a WS
 * handshake (see backend/api/ws.py for the matching server-side check).
 */

export type RuntimeState = "OFFLINE" | "STARTING" | "ONLINE" | "STOPPING" | "ERROR";

export interface RuntimeSummary {
  id: number;
  name: string;
  engine_type: string;
  model_id: number | null;
  model_name: string | null;
  host: string;
  port: number;
  executable_path: string | null;
  status: RuntimeState;
  pid: number | null;
  last_error: string | null;
  config_json: Record<string, unknown>;
  source_system: string;
}

export type VerificationStatus =
  | "pending"
  | "verified"
  | "hash_mismatch"
  | "metadata_only"
  | "metadata_mismatch"
  | "not_found"
  | "no_hash_available"
  | "error"
  | null;

export interface VerificationDetails {
  status?: string;
  reason?: string;
  local_sha256?: string | null;
  remote_sha256?: string | null;
  local_size_bytes?: number | null;
  remote_size_bytes?: number | null;
}

export interface ModelSummary {
  id: number;
  name: string;
  file_path: string;
  file_size_bytes: number;
  quantization: string | null;
  param_count: string | null;
  context_length: number | null;
  hf_repo_id: string | null;
  hf_filename: string | null;
  verification_status: VerificationStatus;
  verification_checked_at: string | null;
  verification_details: VerificationDetails;
}

export interface ModelUpdatePayload {
  quantization?: string;
  param_count?: string;
  context_length?: number;
  hf_repo_id?: string;
  hf_filename?: string;
}

export interface ModelComparisonRow {
  metadata: {
    id: number;
    name: string;
    file_size_bytes: number;
    quantization: string | null;
    param_count: string | null;
    context_length: number | null;
    hf_repo_id: string | null;
  };
  estimated_system_impact: {
    estimated_base_mb: number;
    estimated_kv_cache_mb: number;
    estimated_total_mb: number;
    note: string;
  };
  real_world_performance: {
    requests_count: number;
    avg_latency_ms: number | null;
    avg_tokens_per_sec: number | null;
  };
  public_benchmarks: {
    hf_repo_id: string;
    likes: number | null;
    downloads: number | null;
    tags: string[] | null;
    pipeline_tag: string | null;
    eval_results: { task: string | null; dataset: string | null; metric_name: string | null; value: number | null }[];
  } | null;
}

export interface AgentSummary {
  id: number;
  name: string;
  description: string | null;
  agent_type: string;
  agent_backend: string;
  default_runtime_id: number | null;
  config_json: Record<string, unknown>;
  status: string;
}

export interface RuntimeMetrics {
  tokens_per_sec: number | null;
  last_latency_ms: number | null;
  active_slots: number | null;
}

export interface CurrentMappingEntry {
  agent_id: number;
  agent_name: string | null;
  since: string;
  requests_count: number;
}

export type CurrentMapping = Record<string, CurrentMappingEntry[]>;

export interface UsageHistoryEntry {
  id: number;
  runtime_id: number;
  agent_id: number;
  started_at: string;
  ended_at: string | null;
  status: string;
  requests_count: number;
  avg_latency_ms: number | null;
  avg_tokens_per_sec: number | null;
}

export interface OnboardingState {
  llama_cpp_detected: boolean;
  llama_cpp_path: string | null;
  first_model_added: boolean;
  first_runtime_configured: boolean;
  first_agent_created: boolean;
  wizard_completed: boolean;
}

export interface InstallGuidance {
  platform: string;
  releases_url: string;
  note: string;
}

export interface HardwareSnapshot {
  cpu: { percent: number; core_count: number; per_core_percent: number[] | null };
  memory: { used_mb: number; total_mb: number; percent: number };
  gpu: {
    available: boolean;
    name: string | null;
    utilization_percent: number | null;
    vram_used_mb: number | null;
    vram_total_mb: number | null;
  };
  runtimes: {
    runtime_id: number;
    name: string;
    state: RuntimeState;
    tokens_per_sec: number | null;
    active_slots: number | null;
  }[];
}

export interface ChatSummary {
  id: number;
  runtime_id: number;
  agent_id: number | null;
  title: string;
  project_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatMessageItem {
  id: number;
  chat_id: number;
  role: "user" | "assistant" | "system";
  content: string;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  latency_ms: number | null;
  created_at: string;
}

export interface GlobalSearchResult {
  message_id: number;
  chat_id: number;
  chat_title: string | null;
  runtime_id: number | null;
  role: string;
  content: string;
  created_at: string;
}

export interface ProjectSummary {
  id: string;
  name: string;
  local_path: string | null;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export type GitUnavailableReason = "no_local_path" | "git_not_installed" | "not_a_repo" | null;

export interface GitContext {
  available: boolean;
  reason: GitUnavailableReason;
  message: string | null;
}

export interface GitFileStatus {
  path: string;
  status_code: string;
}

export interface GitStatus extends GitContext {
  branch?: string | null;
  ahead?: number | null;
  behind?: number | null;
  staged_count?: number;
  unstaged_count?: number;
  untracked_count?: number;
  files?: GitFileStatus[];
}

export interface GitDiffFile {
  path: string;
  insertions: number;
  deletions: number;
  binary: boolean;
}

export interface GitDiffStat extends GitContext {
  files_changed?: number;
  insertions?: number;
  deletions?: number;
  files?: GitDiffFile[];
}

export interface GitCommit {
  hash: string;
  short_hash: string;
  author: string;
  date: string;
  subject: string;
}

export interface GitLog extends GitContext {
  commits?: GitCommit[];
}

export interface GitCommitResult {
  files_changed: number;
  insertions: number;
  deletions: number;
  commit: GitCommit | null;
}

export interface ProjectRuntimeUsage {
  runtime_id: number;
  exclusive_to_project: boolean;
  project_requests: number;
  total_requests_on_runtime: number;
  request_share: number | null;
  runtime_avg_metrics: {
    avg_cpu_percent: number | null;
    avg_ram_used_mb: number | null;
    avg_gpu_percent: number | null;
    avg_vram_used_mb: number | null;
  };
  estimated_project_metrics: {
    estimated_cpu_percent: number | null;
    estimated_ram_used_mb: number | null;
    estimated_gpu_percent: number | null;
    estimated_vram_used_mb: number | null;
  };
}

export interface ProjectHardwareUsage {
  runtimes: ProjectRuntimeUsage[];
  exclusive_runtime_count: number;
  shared_runtime_count: number;
  total_estimated_ram_used_mb: number;
  total_estimated_cpu_percent: number;
  note: string;
}

export interface AgentBackendInfo {
  backend_id: string;
  display_name: string;
  brand_color: string;
  detected: boolean;
  detected_path: string | null;
  detected_version: string | null;
}

// ---- Settings -> Browse (offline picker + online discovery) ----

export interface FsEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number | null;
}

export interface FsListing {
  path: string; // "" = the drive list (Windows)
  parent: string | null; // "" = go up to the drive list; null = nothing above
  entries: FsEntry[];
  roots: { name: string; path: string }[];
  truncated: boolean;
}

export interface FsListParams {
  path?: string | null;
  kind?: "dir" | "file" | "any";
  extensions?: string[];
  executableOnly?: boolean;
  showHidden?: boolean;
}

export interface HfModelResult {
  repo_id: string;
  author: string;
  name: string;
  downloads: number;
  likes: number;
  updated: string | null;
  gated: boolean;
  url: string;
  relevance: number;
}

export interface HfFileCandidate {
  filename: string;
  display_name: string;
  quantization: string | null;
  size_bytes: number | null;
  parts: number;
  verifiable: boolean;
  files: { path: string; size: number | null; sha256: string | null }[];
}

export type DownloadStatus = "queued" | "downloading" | "done" | "error" | "cancelled";

export interface DownloadJob {
  id: string;
  repo_id: string;
  filename: string;
  status: DownloadStatus;
  downloaded_bytes: number;
  total_bytes: number | null;
  current_file: string | null;
  note: string | null;
  speed_bps: number;
  error: string | null;
  local_path: string | null;
  result: { registered?: boolean; model_id?: number; model_name?: string; register_error?: string };
  started_at: number;
  finished_at: number | null;
}

export interface AgentRepoResult {
  full_name: string;
  description: string | null;
  stars: number;
  url: string | null;
  language: string | null;
  license: string | null;
  last_push: string | null;
  archived: boolean;
  owner_type: string | null;
  topics: string[];
}

export interface InstallCommandCandidate {
  command: string;
  runnable: boolean;
  reason: string | null;
  /** Only set once the command is known to be runnable: a plain package
   * install, or a `curl|wget -> bash|sh` script installer. */
  kind: "package" | "script" | null;
  /** Script installers only: the host the script is downloaded from. */
  host: string | null;
  /** Script installers only: environment notes (e.g. "bash here is the WSL launcher"). */
  notes: string[];
}

export interface AgentInstallCandidates {
  full_name: string;
  repo_url: string;
  commands: InstallCommandCandidate[];
}

export interface AgentInstallJob {
  id: string;
  repo: string;
  command: string;
  kind: "package" | "script";
  status: "running" | "done" | "error";
  log: string[];
  returncode: number | null;
  error: string | null;
  started_at: number;
  finished_at: number | null;
}

export interface LocalAgentRegistration {
  agent: AgentSummary;
  created: boolean;
  path: string;
  detected_version: string | null;
}

const BASE_URL = "http://127.0.0.1:8420";
const TOKEN_STORAGE_KEY = "lai_access_token";

export function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function setStoredToken(token: string): void {
  localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

export function clearStoredToken(): void {
  localStorage.removeItem(TOKEN_STORAGE_KEY);
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

/** FastAPI errors arrive as `{"detail": "..."}`; this returns just the
 * human-readable part (falls back to the raw message for anything else). */
export function apiErrorMessage(err: unknown, fallback = "Something went wrong."): string {
  if (!(err instanceof Error)) return fallback;
  try {
    const parsed = JSON.parse(err.message);
    if (typeof parsed?.detail === "string") return parsed.detail;
  } catch {
    // not JSON -- use the message as-is
  }
  return err.message || fallback;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getStoredToken();
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers ?? {}),
    },
  });

  if (res.status === 401) {
    clearStoredToken();
    throw new ApiError(401, "Access token is invalid or missing.");
  }
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, body || `Request failed (${res.status})`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ---- Structured event log (backend/core/events/) ----

export interface EventSource {
  system: string;
  project_id: string | null;
  agent_id: string | null;
  session_id: string | null;
  task_id: string | null;
}

export interface LogEvent {
  id: number;
  event_id: string;
  event_type: string;
  timestamp: string;
  device_id: string | null;
  runtime_id: number | null;
  agent_id: number | null;
  session_id: number | null;
  source: EventSource;
  metadata: Record<string, unknown>;
}

export interface EventTypeInfo {
  event_type: string;
  description: string;
}

export interface ListEventsParams {
  /** An exact type ("runtime.started"), a comma-separated list, or a
   * "runtime."-style prefix to match a whole category. */
  eventType?: string;
  runtimeId?: number;
  agentId?: number;
  sessionId?: number;
  since?: string;
  /** Pagination cursor: the smallest `id` already seen, to page further back. */
  beforeId?: number;
  limit?: number;
}

// ---- MCP servers (backend/core/mcp/) ----

export type McpTransport = "stdio" | "http" | "sse";
export type McpStatusName = "DISCONNECTED" | "CONNECTING" | "CONNECTED" | "ERROR";

export interface McpServer {
  id: number;
  name: string;
  description: string | null;
  transport: McpTransport;
  command: string | null;
  args: string[];
  /** Names only -- env values are write-only and never returned by the backend. */
  env_keys: string[];
  url: string | null;
  /** Names only, same as env_keys. */
  header_keys: string[];
  is_remote: boolean;
  enabled: boolean;
  status: McpStatusName;
  last_error: string | null;
  tools_count: number;
  server_info: { name?: string | null; version?: string | null; protocol_version?: string | null };
  source_system: string;
  project_id: string | null;
  agent_id: string | null;
  session_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface McpTool {
  name: string;
  title?: string;
  description: string;
  input_schema: Record<string, unknown>;
  annotations?: Record<string, unknown>;
}

export interface McpLifecycleResult {
  state: McpStatusName;
  tools_count: number;
  error: string | null;
  server_info: McpServer["server_info"];
  server: McpServer;
}

/** Create body: full config. Update body: any subset; for `env`/`headers` a
 * key set to `null` deletes it and absent keys are left untouched. */
export interface McpServerPayload {
  name?: string;
  description?: string | null;
  transport?: McpTransport;
  command?: string | null;
  args?: string[];
  env?: Record<string, string | null>;
  url?: string | null;
  headers?: Record<string, string | null>;
  enabled?: boolean;
}

// ---- Permission Engine (backend/core/permissions/) ----

export type PermissionScopeType = "mcp_server" | "mcp_tool";
export type RiskLevel = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type PermissionDecision = "allow_once" | "allow_session" | "allow_always" | "deny";

export interface PendingPermissionRequest {
  id: string;
  scope_type: PermissionScopeType;
  scope_key: string;
  risk_level: RiskLevel;
  session_id: number | null;
  agent_id: number | null;
  description: string | null;
  requested_at: string;
}

export interface PermissionGrant {
  id: number;
  scope_type: PermissionScopeType;
  scope_key: string;
  risk_level: RiskLevel;
  decision: PermissionDecision;
  description: string | null;
  agent_id: number | null;
  session_id: number | null;
  granted_at: string;
  expires_at: string | null;
  /** true when this grant still auto-applies (allow_session/allow_always, not yet revoked). */
  active: boolean;
}

export interface CheckPermissionResult {
  decision: "allow" | "deny";
  source: "existing_grant" | "live_decision" | "timeout";
  grant_id: number | null;
  request_id: string | null;
  decision_kind?: PermissionDecision;
}

export const api = {
  bootstrapToken: () => request<{ access_token: string }>("/api/v1/auth/bootstrap-token"),

  listRuntimes: () => request<RuntimeSummary[]>("/api/v1/runtimes"),
  createRuntime: (body: {
    name: string;
    executable_path: string;
    model_id?: number | null;
    host?: string;
    port: number;
    config_json?: Record<string, unknown>;
  }) => request<RuntimeSummary>("/api/v1/runtimes", { method: "POST", body: JSON.stringify(body) }),
  startRuntime: (id: number) => request(`/api/v1/runtimes/${id}/start`, { method: "POST" }),
  stopRuntime: (id: number) => request(`/api/v1/runtimes/${id}/stop`, { method: "POST" }),
  restartRuntime: (id: number) => request(`/api/v1/runtimes/${id}/restart`, { method: "POST" }),
  getRuntimeMetrics: (id: number) => request<RuntimeMetrics>(`/api/v1/runtimes/${id}/metrics`),
  getRuntimeLogs: (id: number, n = 200) =>
    request<{ lines: string[] }>(`/api/v1/runtimes/${id}/logs?n=${n}`),
  updateRuntimeConfig: (
    id: number,
    body: { name?: string; host?: string; port?: number; model_id?: number; executable_path?: string; config_json?: Record<string, unknown> }
  ) => request<RuntimeSummary>(`/api/v1/runtimes/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteRuntime: (id: number) => request<{ deleted: boolean }>(`/api/v1/runtimes/${id}`, { method: "DELETE" }),

  listModels: () => request<ModelSummary[]>("/api/v1/models"),
  getModel: (id: number) => request<ModelSummary>(`/api/v1/models/${id}`),
  updateModel: (id: number, body: ModelUpdatePayload) =>
    request<ModelSummary>(`/api/v1/models/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteModel: (id: number) =>
    request<{ deleted: boolean; unlinked_runtime_ids: number[] }>(`/api/v1/models/${id}`, { method: "DELETE" }),
  scanModels: (folder: string) =>
    request<ModelSummary[]>("/api/v1/models/scan", { method: "POST", body: JSON.stringify({ folder }) }),
  compareModels: (modelIds: number[]) =>
    request<{ internet_available: boolean; rows: ModelComparisonRow[] }>("/api/v1/models/compare", {
      method: "POST",
      body: JSON.stringify({ model_ids: modelIds }),
    }),
  getConnectivity: (force = false) =>
    request<{ online: boolean }>(`/api/v1/system/connectivity${force ? "?force=true" : ""}`),
  startModelVerification: (id: number) =>
    request<{ model_id: number; verification_status: VerificationStatus }>(`/api/v1/models/${id}/verify`, {
      method: "POST",
    }),
  getModelVerification: (id: number) =>
    request<{
      model_id: number;
      verification_status: VerificationStatus;
      verification_checked_at: string | null;
      verification_details: VerificationDetails;
    }>(`/api/v1/models/${id}/verify`),

  listAgents: () => request<AgentSummary[]>("/api/v1/agents"),
  getAgent: (id: number) => request<AgentSummary>(`/api/v1/agents/${id}`),
  createAgent: (body: { name: string; description?: string; agent_type?: string; agent_backend?: string }) =>
    request<{ id: number; name: string }>("/api/v1/agents", { method: "POST", body: JSON.stringify(body) }),
  updateAgentConfig: (
    id: number,
    body: { name?: string; description?: string; agent_backend?: string; config_json?: Record<string, unknown> }
  ) => request<AgentSummary>(`/api/v1/agents/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteAgent: (id: number) => request<{ deleted: boolean }>(`/api/v1/agents/${id}`, { method: "DELETE" }),
  listAgentBackends: () => request<AgentBackendInfo[]>("/api/v1/agent-backends"),
  configureAgent: (id: number, body: { runtime_id: number; model_name?: string; api_key?: string }) =>
    request<{ agent: AgentSummary; commands_ran: string[] }>(`/api/v1/agents/${id}/configure`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  attachAgent: (runtimeId: number, agentId: number) =>
    request(`/api/v1/runtimes/${runtimeId}/agents/${agentId}/attach`, { method: "POST" }),
  detachSession: (linkId: number) => request(`/api/v1/sessions/${linkId}/detach`, { method: "POST" }),

  getCurrentMapping: () => request<CurrentMapping>("/api/v1/analytics/current-mapping"),
  getUsageHistory: (params: { runtimeId?: number; agentId?: number; limit?: number } = {}) => {
    const qs = new URLSearchParams();
    if (params.runtimeId != null) qs.set("runtime_id", String(params.runtimeId));
    if (params.agentId != null) qs.set("agent_id", String(params.agentId));
    if (params.limit != null) qs.set("limit", String(params.limit));
    return request<UsageHistoryEntry[]>(`/api/v1/analytics/history?${qs.toString()}`);
  },

  getOnboardingState: () => request<OnboardingState>("/api/v1/onboarding/state"),
  patchOnboardingState: (body: Partial<OnboardingState>) =>
    request<OnboardingState>("/api/v1/onboarding/state", { method: "PATCH", body: JSON.stringify(body) }),
  detectLlamaCpp: () => request<OnboardingState>("/api/v1/onboarding/detect-llama-cpp", { method: "POST" }),
  verifyExecutablePath: (path: string) =>
    request<{ valid: boolean; path: string | null }>("/api/v1/onboarding/verify-path", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  getInstallGuidance: () => request<InstallGuidance>("/api/v1/onboarding/install-guidance"),

  listChats: (params: { runtimeId?: number; agentId?: number; projectId?: string; unassigned?: boolean } = {}) => {
    const qs = new URLSearchParams();
    if (params.runtimeId != null) qs.set("runtime_id", String(params.runtimeId));
    if (params.agentId != null) qs.set("agent_id", String(params.agentId));
    if (params.projectId != null) qs.set("project_id", params.projectId);
    if (params.unassigned) qs.set("unassigned", "true");
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return request<ChatSummary[]>(`/api/v1/chats${suffix}`);
  },
  createChat: (body: { runtime_id: number; agent_id?: number | null; title?: string }) =>
    request<ChatSummary>("/api/v1/chats", { method: "POST", body: JSON.stringify(body) }),
  deleteChat: (id: number) => request(`/api/v1/chats/${id}`, { method: "DELETE" }),
  promoteChat: (
    chatId: number,
    body: { project_id?: string; new_project_name?: string; local_path?: string }
  ) => request<ChatSummary>(`/api/v1/chats/${chatId}/promote`, { method: "POST", body: JSON.stringify(body) }),
  getChatMessages: (chatId: number) => request<ChatMessageItem[]>(`/api/v1/chats/${chatId}/messages`),
  sendChatMessage: (chatId: number, content: string, opts: { temperature?: number; max_tokens?: number } = {}) =>
    request<ChatMessageItem>(`/api/v1/chats/${chatId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content, ...opts }),
    }),
  searchInChat: (chatId: number, q: string) =>
    request<ChatMessageItem[]>(`/api/v1/chats/${chatId}/search?q=${encodeURIComponent(q)}`),
  globalSearch: (q: string) =>
    request<GlobalSearchResult[]>(`/api/v1/search?q=${encodeURIComponent(q)}`),

  listProjects: () => request<ProjectSummary[]>("/api/v1/projects"),
  createProject: (body: { name: string; local_path?: string; description?: string }) =>
    request<ProjectSummary>("/api/v1/projects", { method: "POST", body: JSON.stringify(body) }),
  getProject: (id: string) => request<ProjectSummary>(`/api/v1/projects/${id}`),
  updateProject: (id: string, body: { name?: string; local_path?: string; description?: string }) =>
    request<ProjectSummary>(`/api/v1/projects/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteProject: (id: string) => request(`/api/v1/projects/${id}`, { method: "DELETE" }),

  getProjectGitStatus: (id: string) => request<GitStatus>(`/api/v1/projects/${id}/git/status`),
  getProjectGitDiff: (id: string, staged = false) =>
    request<GitDiffStat>(`/api/v1/projects/${id}/git/diff${staged ? "?staged=true" : ""}`),
  getProjectGitLog: (id: string, limit = 20) =>
    request<GitLog>(`/api/v1/projects/${id}/git/log?limit=${limit}`),
  commitProjectGit: (id: string, message: string) =>
    request<GitCommitResult>(`/api/v1/projects/${id}/git/commit`, {
      method: "POST",
      body: JSON.stringify({ message }),
    }),
  getProjectHardwareUsage: (id: string) =>
    request<ProjectHardwareUsage>(`/api/v1/projects/${id}/hardware-usage`),

  // ---- Settings -> Browse ----
  fsList: (params: FsListParams = {}) => {
    const qs = new URLSearchParams();
    // `path=` (empty) is meaningful -- it asks for the Windows drive list --
    // so only omit the param when it's null/undefined.
    if (params.path != null) qs.set("path", params.path);
    if (params.kind) qs.set("kind", params.kind);
    if (params.extensions?.length) qs.set("ext", params.extensions.join(","));
    if (params.executableOnly) qs.set("executable_only", "true");
    if (params.showHidden) qs.set("hidden", "true");
    return request<FsListing>(`/api/v1/discover/fs/list?${qs.toString()}`);
  },
  registerLocalModel: (path: string) =>
    request<ModelSummary[]>("/api/v1/discover/models/register-local", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  registerLocalAgent: (path: string) =>
    request<LocalAgentRegistration>("/api/v1/discover/agents/register-local", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  getModelsDir: () => request<{ path: string }>("/api/v1/discover/models/dir"),
  setModelsDir: (path: string) =>
    request<{ path: string }>("/api/v1/discover/models/dir", { method: "PUT", body: JSON.stringify({ path }) }),
  searchHfModels: (q: string) =>
    request<HfModelResult[]>(`/api/v1/discover/models/search?q=${encodeURIComponent(q)}`),
  listHfFiles: (repo: string) =>
    request<HfFileCandidate[]>(`/api/v1/discover/models/files?repo=${encodeURIComponent(repo)}`),
  startModelDownload: (repo_id: string, filename: string) =>
    request<DownloadJob>("/api/v1/discover/models/download", {
      method: "POST",
      body: JSON.stringify({ repo_id, filename }),
    }),
  listDownloads: () => request<DownloadJob[]>("/api/v1/discover/downloads"),
  cancelDownload: (id: string) =>
    request<{ cancelling: boolean }>(`/api/v1/discover/downloads/${id}/cancel`, { method: "POST" }),
  searchAgentRepos: (q: string) =>
    request<AgentRepoResult[]>(`/api/v1/discover/agents/search?q=${encodeURIComponent(q)}`),
  getAgentInstallCandidates: (repo: string) =>
    request<AgentInstallCandidates>(`/api/v1/discover/agents/install-candidates?repo=${encodeURIComponent(repo)}`),
  installAgent: (repo: string, command: string) =>
    request<AgentInstallJob>("/api/v1/discover/agents/install", {
      method: "POST",
      body: JSON.stringify({ repo, command, confirmed: true }),
    }),
  getAgentInstall: (id: string) => request<AgentInstallJob>(`/api/v1/discover/agents/install/${id}`),

  // ---- Structured event log ----
  listEvents: (params: ListEventsParams = {}) => {
    const qs = new URLSearchParams();
    if (params.eventType) qs.set("event_type", params.eventType);
    if (params.runtimeId != null) qs.set("runtime_id", String(params.runtimeId));
    if (params.agentId != null) qs.set("agent_id", String(params.agentId));
    if (params.sessionId != null) qs.set("session_id", String(params.sessionId));
    if (params.since) qs.set("since", params.since);
    if (params.beforeId != null) qs.set("before_id", String(params.beforeId));
    qs.set("limit", String(params.limit ?? 100));
    return request<LogEvent[]>(`/api/v1/events?${qs.toString()}`);
  },
  getEventTypes: () => request<EventTypeInfo[]>("/api/v1/events/types"),

  // ---- MCP servers ----
  listMcpServers: () => request<McpServer[]>("/api/v1/mcp/servers"),
  createMcpServer: (body: McpServerPayload & { name: string }) =>
    request<McpServer>("/api/v1/mcp/servers", { method: "POST", body: JSON.stringify(body) }),
  updateMcpServer: (id: number, body: McpServerPayload) =>
    request<McpServer>(`/api/v1/mcp/servers/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteMcpServer: (id: number) => request<{ deleted: boolean }>(`/api/v1/mcp/servers/${id}`, { method: "DELETE" }),
  connectMcpServer: (id: number) => request<McpLifecycleResult>(`/api/v1/mcp/servers/${id}/connect`, { method: "POST" }),
  disconnectMcpServer: (id: number) =>
    request<McpLifecycleResult>(`/api/v1/mcp/servers/${id}/disconnect`, { method: "POST" }),
  listMcpServerTools: (id: number) => request<{ server_id: number; tools: McpTool[] }>(`/api/v1/mcp/servers/${id}/tools`),

  // ---- Permission Engine ----
  /** Blocks server-side until a decision is made or `timeoutSeconds` elapses. */
  checkPermission: (body: {
    scope_type: PermissionScopeType;
    scope_key: string;
    risk_level: RiskLevel;
    session_id?: number;
    agent_id?: number;
    description?: string;
    timeout_seconds?: number;
  }) => request<CheckPermissionResult>("/api/v1/permissions/check", { method: "POST", body: JSON.stringify(body) }),
  listPendingPermissions: () => request<PendingPermissionRequest[]>("/api/v1/permissions/pending"),
  resolvePermissionRequest: (requestId: string, decision: PermissionDecision) =>
    request<{ resolved: boolean }>(`/api/v1/permissions/requests/${requestId}/resolve`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    }),
  listPermissionGrants: (opts: { scopeType?: PermissionScopeType; scopeKey?: string; activeOnly?: boolean } = {}) => {
    const params = new URLSearchParams();
    if (opts.scopeType) params.set("scope_type", opts.scopeType);
    if (opts.scopeKey) params.set("scope_key", opts.scopeKey);
    if (opts.activeOnly) params.set("active_only", "true");
    const qs = params.toString();
    return request<PermissionGrant[]>(`/api/v1/permissions/grants${qs ? `?${qs}` : ""}`);
  },
  revokePermissionGrant: (id: number) => request<PermissionGrant>(`/api/v1/permissions/grants/${id}`, { method: "DELETE" }),
};

/**
 * Opens the metrics WebSocket and calls `onMessage` for every snapshot.
 * Returns a cleanup function that closes the socket -- call it from a
 * useEffect cleanup.
 */
export function connectMetricsSocket(onMessage: (snapshot: HardwareSnapshot) => void): () => void {
  const token = getStoredToken() ?? "";
  const wsUrl = `${BASE_URL.replace("http", "ws")}/ws/metrics?token=${encodeURIComponent(token)}`;
  const socket = new WebSocket(wsUrl);

  socket.onmessage = (event) => {
    try {
      onMessage(JSON.parse(event.data));
    } catch {
      // malformed frame -- drop it, next snapshot arrives in ~1.5s anyway
    }
  };

  return () => socket.close();
}

/**
 * Opens the live event-log WebSocket (backend/api/ws.py's /ws/events)
 * and calls `onEvent` for every event as it's emitted. Every event this
 * delivers is also already durably persisted by the time it arrives --
 * see api.listEvents for the history view of the same stream -- so a
 * consumer that reconnects after a gap should backfill with that rather
 * than expect this socket to replay anything it missed.
 */
export function connectEventsSocket(onEvent: (event: LogEvent) => void): () => void {
  const token = getStoredToken() ?? "";
  const wsUrl = `${BASE_URL.replace("http", "ws")}/ws/events?token=${encodeURIComponent(token)}`;
  const socket = new WebSocket(wsUrl);

  socket.onmessage = (event) => {
    try {
      onEvent(JSON.parse(event.data));
    } catch {
      // malformed frame -- drop it, the next one is close behind
    }
  };

  return () => socket.close();
}

/**
 * Builds the URL for a live agent-terminal WebSocket session (see
 * backend/api/ws.py's /ws/agent-terminal/{agent_id}). Left as a URL
 * builder rather than a connect-and-return-cleanup helper like
 * connectMetricsSocket, since AgentTerminal needs to drive the socket's
 * lifecycle itself (xterm.js writes/resizes tie directly to it).
 */
export function buildAgentTerminalWsUrl(agentId: number, runtimeId: number, cols: number, rows: number): string {
  const token = getStoredToken() ?? "";
  const params = new URLSearchParams({
    token,
    runtime_id: String(runtimeId),
    cols: String(cols),
    rows: String(rows),
  });
  return `${BASE_URL.replace("http", "ws")}/ws/agent-terminal/${agentId}?${params.toString()}`;
}
