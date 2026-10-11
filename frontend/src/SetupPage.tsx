import { useState } from "react";
import { BrowseTab } from "./components/BrowseTab";
import { OnboardingWizard } from "./OnboardingWizard";
import { filterSetupOptions, type SetupFilter, type SetupOptionId } from "./setup-options";

const FILTERS: { id: SetupFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "llm", label: "LLM" },
  { id: "agent", label: "Agent" },
];

/**
 * Setup: first the options (what do you want to do?), then only the steps
 * of the option you opened. Finding and adding models/agents lives here --
 * it used to be a second copy under Settings -> Browse.
 */
export function SetupPage({ onDone }: { onDone: () => void }) {
  const [filter, setFilter] = useState<SetupFilter>("all");
  const [choice, setChoice] = useState<SetupOptionId | null>(null);

  if (choice === "run-llm") {
    return (
      <OnboardingWizard
        onComplete={onDone}
        onCancel={() => setChoice(null)}
        cancelLabel="← All setup options"
      />
    );
  }

  if (choice === "add-model" || choice === "add-agent") {
    const agents = choice === "add-agent";
    return (
      <div className="main">
        <section>
          <button className="wizard-back-link" onClick={() => setChoice(null)}>
            ← All setup options
          </button>
          <div className="section-heading">
            <h2>{agents ? "Add an agent" : "Add a model"}</h2>
          </div>
          <BrowseTab kind={agents ? "agents" : "models"} />
        </section>
      </div>
    );
  }

  const options = filterSetupOptions(filter);
  return (
    <div className="main">
      <section>
        <div className="section-heading">
          <h2>Setup</h2>
          <div className="segmented" role="group" aria-label="Show">
            {FILTERS.map((f) => (
              <button
                key={f.id}
                className={filter === f.id ? "selected" : ""}
                aria-pressed={filter === f.id}
                onClick={() => setFilter(f.id)}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
        <div className="setup-cards">
          {options.map((o) => (
            <button key={o.id} className="setup-card" onClick={() => setChoice(o.id)}>
              <span className="setup-card-kind">{o.kind === "llm" ? "LLM" : "Agent"}</span>
              <span className="setup-card-title">{o.title}</span>
              <span className="setup-card-blurb">{o.blurb}</span>
              <span className="setup-card-steps">{o.steps.join("  ›  ")}</span>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
