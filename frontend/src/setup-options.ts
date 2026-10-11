/** The things you can do from the Setup page. Kept as plain data so the
 * LLM/agent filter is a tested pure function, not JSX logic. */
export type SetupKind = "llm" | "agent";
export type SetupFilter = "all" | SetupKind;
export type SetupOptionId = "run-llm" | "add-model" | "add-agent";

export interface SetupOption {
  id: SetupOptionId;
  kind: SetupKind;
  title: string;
  blurb: string;
  /** What the user will go through, shown on the card before opening it. */
  steps: string[];
}

export const SETUP_OPTIONS: SetupOption[] = [
  {
    id: "run-llm",
    kind: "llm",
    title: "Run an LLM",
    blurb: "Start a model on this computer with llama.cpp.",
    steps: ["Find llama.cpp", "Pick a model", "Start it"],
  },
  {
    id: "add-model",
    kind: "llm",
    title: "Add a model",
    blurb: "Use a .gguf file you already have, or search and download one.",
    steps: ["This computer or online", "Choose or search"],
  },
  {
    id: "add-agent",
    kind: "agent",
    title: "Add an agent",
    blurb: "Register an agent you installed, or search and install one.",
    steps: ["This computer or online", "Choose or search"],
  },
];

export function filterSetupOptions(filter: SetupFilter, options: SetupOption[] = SETUP_OPTIONS): SetupOption[] {
  return filter === "all" ? options : options.filter((o) => o.kind === filter);
}
