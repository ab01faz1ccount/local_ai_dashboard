import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ModelCompareTable } from "../src/components/ModelCompareTable";
import type { GgufFacts, ModelComparisonRow } from "../src/api";

const facts = (over: Partial<GgufFacts>): GgufFacts => ({
  architecture: "qwen3", name: "Demo", size_label: "9B", finetune: null, parameters: 8_950_000_000, parameters_label: "8.95 B",
  file_type: "Q4_K_M", tensor_mix: [{ type: "Q4_K", share: 0.83, tensors: 200 }, { type: "Q6_K", share: 0.17, tensors: 40 }],
  bits_per_weight: 4.9, layers: 36, embedding_length: 4096, attention_heads: 32, kv_heads: 8, context_length: 262144, experts: null,
  vocab_size: 151936, has_chat_template: true, kv_cache: { bytes_per_token: 147456, layers_with_cache: 36, kv_heads: 8, head_dim: 128, note: null },
  parts: 1, tensor_count: 440, ...over,
});

const row = (id: number, name: string, gguf: GgufFacts | null, repo: string | null = "unsloth/Demo-GGUF", gguf_error: string | null = null): ModelComparisonRow => ({
  metadata: { id, name, file_size_bytes: 5e9, quantization: gguf?.file_type ?? null, param_count: gguf?.parameters_label ?? null, context_length: gguf?.context_length ?? null, hf_repo_id: repo },
  gguf, gguf_error,
  estimated_system_impact: { estimated_base_mb: 3800, estimated_kv_cache_mb: 300, estimated_total_mb: 4100, note: "n" },
  hardware_fit: { label: "GOOD", target: "gpu", ratio: 0.5, capacity_mb: 8192, estimated_mb: 4100, reason: "r" },
  real_world_performance: { requests_count: 0, avg_latency_ms: null, avg_tokens_per_sec: null },
  public_benchmarks: null,
  knowledge: repo ? { status: "linked", repo, profile: { id: repo, label: repo, url: null, facts: [] } as never, error: null } : undefined,
});

const q4 = facts({});
const q6 = facts({
  file_type: "Q6_K", bits_per_weight: 6.6,
  tensor_mix: [{ type: "Q6_K", share: 0.99, tensors: 240 }, { type: "F32", share: 0.01, tensors: 100 }],
});

describe("ModelCompareTable: what the files themselves say", () => {
  it("shows exact per-file facts and highlights only the rows that differ", () => {
    render(<ModelCompareTable internetAvailable rows={[row(1, "q4", q4), row(2, "q6", q6)]} />);
    const differs = (label: string) => screen.getByText(label).closest("tr")!.className.includes("compare-differs");
    expect(differs("Bits per weight")).toBe(true);
    expect(differs("Weight types")).toBe(true);
    expect(differs("Layers")).toBe(false);
    expect(differs("Architecture")).toBe(false);
    expect(differs("Parameters (exact)")).toBe(false);
    expect(screen.getByText("Q4_K 83% · Q6_K 17%")).toBeInTheDocument();
    expect(screen.getByText("4.90 bits")).toBeInTheDocument();
    expect(screen.getByText("6.60 bits")).toBeInTheDocument();
  });

  it("no longer leaves Params and Context blank when the file was read", () => {
    render(<ModelCompareTable internetAvailable rows={[row(1, "q4", q4)]} />);
    expect(screen.getByText("Params").closest("tr")!.textContent).toContain("8.95 B");
    expect(screen.getByText("Context length").closest("tr")!.textContent).toContain("262,144");
  });

  it("explains why the web section is identical when both models share a repository", () => {
    render(<ModelCompareTable internetAvailable rows={[row(1, "q4", q4), row(2, "q6", q6)]} />);
    expect(screen.getByText(/come from the same repository \(unsloth\/Demo-GGUF\)/)).toBeInTheDocument();
    expect(screen.getByText(/describes the whole\s+repository, not one quantization/)).toBeInTheDocument();
  });

  it("does not show that note for models from different repositories", () => {
    render(<ModelCompareTable internetAvailable rows={[row(1, "a", q4, "a/x"), row(2, "b", q6, "b/y")]} />);
    expect(screen.queryByText(/same repository/)).toBeNull();
  });

  it("says why when a file could not be read, and keeps the table", () => {
    render(<ModelCompareTable internetAvailable={false} rows={[row(1, "gone", null, null, "The file can't be read: No such file")]} />);
    expect(screen.getByText(/The file can't be read: No such file/)).toBeInTheDocument();
    expect(screen.getByText("Estimated system impact")).toBeInTheDocument();
  });

  it("a file that can't be read among readable ones is reported in its own column", () => {
    render(<ModelCompareTable internetAvailable={false} rows={[row(1, "ok", q4, null), row(2, "bad", null, null, "The file is empty.")]} />);
    expect(screen.getByText("The file is empty.")).toBeInTheDocument();
    expect(screen.getByText("Could not read")).toBeInTheDocument();
  });

  it("rows from an older backend without file facts still render", () => {
    const r = row(1, "old", null, null);
    delete (r as { gguf?: unknown }).gguf;
    delete (r as { gguf_error?: unknown }).gguf_error;
    render(<ModelCompareTable internetAvailable={false} rows={[r]} />);
    expect(screen.getByText(/No file details are available/)).toBeInTheDocument();
  });
});
