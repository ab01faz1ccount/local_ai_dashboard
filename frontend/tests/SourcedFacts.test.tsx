import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FactCell } from "../src/components/FactCell";
import { ModelCompareTable } from "../src/components/ModelCompareTable";
import { AgentCompareTable } from "../src/components/AgentCompareTable";
import type { AgentProfileResult, AgentSummary, KnowledgeFact, KnowledgeProfile, ModelComparisonRow } from "../src/api";

vi.mock("../src/api", async (orig) => {
  const actual = await orig<typeof import("../src/api")>();
  return { ...actual, api: { setAgentRepo: vi.fn(), searchAgentRepos: vi.fn() } };
});
import { api } from "../src/api";
const m = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const fact = (over: Partial<KnowledgeFact>): KnowledgeFact => ({
  key: "license", label: "License", value: "apache-2.0", provenance: "source",
  source: { label: "Hugging Face model card", url: "https://huggingface.co/a/b" }, note: null, ...over,
});

describe("FactCell", () => {
  it("shows the value and a link to its source", () => {
    render(<FactCell fact={fact({})} />);
    expect(screen.getByText("apache-2.0")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Hugging Face model card/ })).toHaveAttribute("href", "https://huggingface.co/a/b");
  });
  it("says where a fact came from when it is not the card's own", () => {
    render(<FactCell fact={fact({ provenance: "base_model", note: "from Qwen/Qwen2.5" })} />);
    expect(screen.getByText("from the base model")).toHaveAttribute("title", "from Qwen/Qwen2.5");
    render(<FactCell fact={fact({ provenance: "derived", value: "7B", source: null })} />);
    expect(screen.getByText("guessed from the name")).toBeInTheDocument();
  });
  it("a missing fact says Not published instead of being blank", () => {
    render(<FactCell fact={fact({ value: null, provenance: "missing", note: "The model card does not state this." })} />);
    expect(screen.getByText(/Not published/)).toHaveAttribute("title", "The model card does not state this.");
  });
  it("never renders a link to anywhere but Hugging Face / GitHub over https", () => {
    render(<FactCell fact={fact({ source: { label: "Evil", url: "javascript:alert(1)" } })} />);
    expect(screen.queryByRole("link")).toBeNull();
  });
  it("lists benchmarks one per line", () => {
    render(<FactCell fact={fact({ key: "benchmarks", value: [{ dataset: "MMLU", metric: "accuracy", value: 74.2 }, { dataset: "GSM8K", metric: "acc", value: 90 }] })} />);
    expect(screen.getAllByRole("listitem").map((x) => x.textContent)).toEqual(["MMLU · accuracy: 74.2", "GSM8K · acc: 90"]);
  });
});

const profile = (id: string, facts: KnowledgeFact[]): KnowledgeProfile => ({ kind: "model", id, url: `https://huggingface.co/${id}`, facts, sources: [], fetched_at: 0 });
const row = (id: number, name: string, knowledge?: ModelComparisonRow["knowledge"]): ModelComparisonRow => ({
  metadata: { id, name, file_size_bytes: 4e9, quantization: "Q4_K_M", param_count: "7B", context_length: 4096, hf_repo_id: null },
  estimated_system_impact: { estimated_base_mb: 3800, estimated_kv_cache_mb: 300, estimated_total_mb: 4100, note: "n" },
  hardware_fit: { label: "GOOD", target: "gpu", ratio: 0.5, capacity_mb: 8192, estimated_mb: 4100, reason: "r" },
  real_world_performance: { requests_count: 0, avg_latency_ms: null, avg_tokens_per_sec: null },
  public_benchmarks: null,
  knowledge,
});

describe("ModelCompareTable web facts", () => {
  it("one row per fact, each model in its own column, with sources", () => {
    const a = row(1, "A", { status: "linked", repo: "x/a", error: null, profile: profile("x/a", [fact({ value: "mit" })]) });
    const b = row(2, "B", { status: "linked", repo: "x/b", error: null, profile: profile("x/b", [fact({ value: "apache-2.0" })]) });
    render(<ModelCompareTable rows={[a, b]} internetAvailable />);
    const tr = screen.getByText("License").closest("tr")!;
    expect(within(tr).getByText("mit")).toBeInTheDocument();
    expect(within(tr).getByText("apache-2.0")).toBeInTheDocument();
  });
  it("a model found by searching is marked as a suggestion and can be linked", async () => {
    const onLink = vi.fn();
    const a = row(1, "A", { status: "suggested", repo: "x/a", error: null, profile: profile("x/a", []) });
    render(<ModelCompareTable rows={[a]} internetAvailable onLinkRepo={onLink} />);
    expect(screen.getByText(/best match from searching its name/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Link it" }));
    expect(onLink).toHaveBeenCalledWith(1, "x/a");
  });
  it("says so when nothing matched, the lookup failed, or the app is offline", () => {
    render(<ModelCompareTable rows={[row(1, "A", { status: "none", repo: null, profile: null, error: null }), row(2, "B", { status: "error", repo: "x/b", profile: null, error: "rate limited" }), row(3, "C", { status: "offline", repo: null, profile: null, error: null })]} internetAvailable />);
    expect(screen.getByText(/No matching Hugging Face repo/)).toBeInTheDocument();
    expect(screen.getByText("rate limited")).toBeInTheDocument();
    expect(screen.getByText(/Offline — can't look this up/)).toBeInTheDocument();
  });
});

const agent = (id: number, name: string, backend = "generic"): AgentSummary => ({
  id, name, description: null, agent_type: "generic", agent_backend: backend, default_runtime_id: null, config_json: {}, status: "INACTIVE",
});
const agentProfile = (over: Partial<AgentProfileResult>): AgentProfileResult => ({ agent_id: 1, status: "ok", repo: "acme/agent", repo_origin: "known", profile: null, error: null, ...over });

describe("AgentCompareTable", () => {
  it("shows capabilities with their source for a linked agent", () => {
    const p: KnowledgeProfile = { ...profile("acme/agent", [fact({ key: "capabilities", label: "What it can do", value: ["Runs code", "Edits files"] })]), kind: "agent", url: "https://github.com/acme/agent" };
    render(<AgentCompareTable agents={[agent(1, "OI", "open_interpreter")]} backends={[{ backend_id: "open_interpreter", display_name: "Open Interpreter", brand_color: "#fff", detected: true, detected_path: "/x", detected_version: "0.4.3" }]} profiles={{ 1: agentProfile({ profile: p }) }} onLinked={() => {}} />);
    expect(screen.getByText("Runs code")).toBeInTheDocument();
    expect(screen.getByText(/✓ 0\.4\.3/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /acme\/agent/ })).toHaveAttribute("href", "https://github.com/acme/agent");
  });
  it("an agent with no known repository asks for one instead of guessing", async () => {
    const onLinked = vi.fn();
    m.setAgentRepo.mockResolvedValue({});
    render(<AgentCompareTable agents={[agent(1, "Mine")]} backends={[]} profiles={{ 1: agentProfile({ status: "unlinked", repo: null, repo_origin: null }) }} onLinked={onLinked} />);
    expect(screen.getByText(/Not linked to a GitHub repository yet/)).toBeInTheDocument();
    const input = screen.getByLabelText("GitHub repository for Mine");
    const link = screen.getByRole("button", { name: "Link" });
    expect(link).toBeDisabled();
    await userEvent.type(input, "not a repo");
    expect(link).toBeDisabled();
    await userEvent.clear(input);
    await userEvent.type(input, "acme/agent");
    await userEvent.click(link);
    expect(m.setAgentRepo).toHaveBeenCalledWith(1, "acme/agent");
    expect(onLinked).toHaveBeenCalled();
  });
  it("Search GitHub offers candidates to pick from", async () => {
    m.searchAgentRepos.mockResolvedValue([{ full_name: "acme/agent", description: "d", stars: 1200, url: null, language: null, license: null, last_push: null, archived: false, owner_type: null, topics: [] }]);
    m.setAgentRepo.mockResolvedValue({});
    render(<AgentCompareTable agents={[agent(1, "Mine")]} backends={[]} profiles={{ 1: agentProfile({ status: "unlinked", repo: null, repo_origin: null }) }} onLinked={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Search GitHub" }));
    await userEvent.click(await screen.findByRole("button", { name: "acme/agent" }));
    expect(m.setAgentRepo).toHaveBeenCalledWith(1, "acme/agent");
  });
});
