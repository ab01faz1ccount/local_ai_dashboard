import { useEffect, useRef, useState } from "react";
import {
  api,
  apiErrorMessage,
  type AgentBackendInfo,
  type AgentSummary,
  type InstallGuidance,
  type ModelSummary,
  type OnboardingState,
  type RuntimeSummary,
} from "./api";
import { FileBrowserDialog } from "./components/FileBrowserDialog";
import { OnlineModelSearch } from "./components/OnlineModelSearch";

const STEP_COUNT = 4;
const START_POLL_TIMEOUT_MS = 20000;
const START_POLL_INTERVAL_MS = 800;

/**
 * Setup: find/verify llama.cpp -> pick a model -> create AND ACTUALLY
 * START a runtime -> create/attach an agent.
 *
 * Used to be a first-run gate the whole app was stuck behind; it's now
 * just the "Setup" nav item, reachable any time and safe to leave
 * half-finished (that's what `onCancel` is for -- nothing here is lost,
 * every step writes to the backend as it happens, not at the end).
 *
 * Every "Continue" here is gated on a real backend result, not just a
 * non-empty field: the LLM step doesn't let you move on until the
 * runtime is actually observed ONLINE, and the agent step actually opens
 * the llm_agent_sessions link rather than just creating a database row.
 * If the user already has runtimes/agents configured (from a previous
 * partial run, or set up outside the wizard), the wizard offers to use
 * them instead of forcing a from-scratch setup.
 */
export function OnboardingWizard({
  onComplete,
  onCancel,
}: {
  onComplete: () => void;
  onCancel?: () => void;
}) {
  const [step, setStep] = useState(0);
  const [state, setState] = useState<OnboardingState | null>(null);
  const [existingRuntimes, setExistingRuntimes] = useState<RuntimeSummary[]>([]);
  const [existingAgents, setExistingAgents] = useState<AgentSummary[]>([]);
  const [existingModels, setExistingModels] = useState<ModelSummary[]>([]);

  // -- step 0: llama.cpp --
  const [guidance, setGuidance] = useState<InstallGuidance | null>(null);
  const [detecting, setDetecting] = useState(false);
  const [executablePath, setExecutablePath] = useState("");
  const [pathVerified, setPathVerified] = useState(false);
  const [verifying, setVerifying] = useState(false);

  // -- step 1: model --
  const [picker, setPicker] = useState<"executable" | "model-folder" | "model-file" | null>(null);
  const [modelsFolder, setModelsFolder] = useState("");
  const [selectedModelId, setSelectedModelId] = useState<number | null>(null);
  const [scanning, setScanning] = useState(false);

  // -- step 2: runtime --
  const [runtimeName, setRuntimeName] = useState("My first LLM");
  const [port, setPort] = useState(8080);
  const [creatingRuntime, setCreatingRuntime] = useState(false);
  const [runtimeId, setRuntimeId] = useState<number | null>(null);
  const [runtimeStatus, setRuntimeStatus] = useState<string | null>(null);
  const [runtimeConfirmedOnline, setRuntimeConfirmedOnline] = useState(false);

  // -- step 3: agent --
  const [agentName, setAgentName] = useState("My first agent");
  const [agentBackends, setAgentBackends] = useState<AgentBackendInfo[]>([]);
  const [agentBackend, setAgentBackend] = useState("generic");
  const [useExistingAgentId, setUseExistingAgentId] = useState<number | "new">("new");
  const [finishing, setFinishing] = useState(false);

  const [error, setError] = useState<string | null>(null);
  const pollTimer = useRef<number | null>(null);

  useEffect(() => {
    (async () => {
      const [s, runtimes, agents, models, backends] = await Promise.all([
        api.getOnboardingState(),
        api.listRuntimes(),
        api.listAgents(),
        api.listModels(),
        api.listAgentBackends(),
      ]);
      setState(s);
      setExistingRuntimes(runtimes);
      setExistingAgents(agents);
      setExistingModels(models);
      setAgentBackends(backends);
      if (s.llama_cpp_path) {
        setExecutablePath(s.llama_cpp_path);
        setPathVerified(true);
      }
    })();
    return () => {
      if (pollTimer.current) window.clearTimeout(pollTimer.current);
    };
  }, []);

  function skipToDashboard() {
    api.patchOnboardingState({ wizard_completed: true }).then(onComplete);
  }

  // -- step 0 --

  async function handleDetect() {
    setDetecting(true);
    setError(null);
    try {
      const s = await api.detectLlamaCpp();
      setState(s);
      if (s.llama_cpp_path) {
        setExecutablePath(s.llama_cpp_path);
        setPathVerified(true);
      } else {
        setPathVerified(false);
        setGuidance(await api.getInstallGuidance());
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Detection failed.");
    } finally {
      setDetecting(false);
    }
  }

  async function handleVerifyPath(pathOverride?: string) {
    const target = pathOverride ?? executablePath;
    setVerifying(true);
    setError(null);
    try {
      const result = await api.verifyExecutablePath(target);
      setPathVerified(result.valid);
      if (!result.valid) {
        setError("That path doesn't point to a real file. Double-check it and try again.");
      } else if (result.path) {
        setExecutablePath(result.path);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not verify that path.");
    } finally {
      setVerifying(false);
    }
  }

  function handlePathEdited(value: string) {
    setExecutablePath(value);
    setPathVerified(false); // any manual edit invalidates the previous verification
  }

  // -- step 1 --

  async function handleScan() {
    setScanning(true);
    setError(null);
    try {
      const models = await api.scanModels(modelsFolder);
      if (models.length === 0) {
        setError("No .gguf files found in that folder.");
        return;
      }
      setExistingModels(models);
      setSelectedModelId(models[0].id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scan failed.");
    } finally {
      setScanning(false);
    }
  }

  /** A model was added outside the folder-scan box (online download, or a
   * picked file/folder): reload the list and select the new one. */
  async function handleModelAdded(modelId?: number) {
    setExistingModels(await api.listModels());
    if (modelId != null) setSelectedModelId(modelId);
  }

  async function handlePickedModelPath(path: string) {
    setPicker(null);
    setError(null);
    try {
      const added = await api.registerLocalModel(path);
      if (added.length === 0) {
        setError("No .gguf files found there.");
        return;
      }
      setExistingModels(await api.listModels());
      setSelectedModelId(added[0].id);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not add that."));
    }
  }

  // -- step 2 --

  async function handleCreateAndStartRuntime() {
    setError(null);
    setCreatingRuntime(true);
    setRuntimeConfirmedOnline(false);
    try {
      const rt = await api.createRuntime({
        name: runtimeName,
        executable_path: executablePath,
        model_id: selectedModelId,
        port,
      });
      setRuntimeId(rt.id);
      setRuntimeStatus(rt.status);

      await api.startRuntime(rt.id);
      await pollUntilOnline(rt.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the runtime.");
      setCreatingRuntime(false);
    }
  }

  function pollUntilOnline(id: number): Promise<void> {
    const deadline = Date.now() + START_POLL_TIMEOUT_MS;
    return new Promise((resolve) => {
      const tick = async () => {
        const runtimes = await api.listRuntimes();
        const rt = runtimes.find((r) => r.id === id);
        setRuntimeStatus(rt?.status ?? null);

        if (rt?.status === "ONLINE") {
          setRuntimeConfirmedOnline(true);
          setCreatingRuntime(false);
          resolve();
          return;
        }
        if (rt?.status === "ERROR") {
          setError(rt.last_error ?? "The runtime failed to start.");
          setCreatingRuntime(false);
          resolve();
          return;
        }
        if (Date.now() > deadline) {
          setError("Timed out waiting for the runtime to come online. Check the logs from the dashboard.");
          setCreatingRuntime(false);
          resolve();
          return;
        }
        pollTimer.current = window.setTimeout(tick, START_POLL_INTERVAL_MS);
      };
      tick();
    });
  }

  // -- step 3 --

  async function handleFinish() {
    if (runtimeId == null) return;
    setError(null);
    setFinishing(true);
    try {
      let agentId: number;
      if (useExistingAgentId === "new") {
        const agent = await api.createAgent({ name: agentName, agent_backend: agentBackend });
        agentId = agent.id;
      } else {
        agentId = useExistingAgentId;
      }
      await api.attachAgent(runtimeId, agentId);
      await api.patchOnboardingState({ wizard_completed: true });
      onComplete();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not finish setup.");
    } finally {
      setFinishing(false);
    }
  }

  if (!state) return null;

  const hasExistingSetup = existingRuntimes.length > 0 || existingAgents.length > 0;

  return (
    <div className="wizard-shell">
      {onCancel && (
        <button className="wizard-back-link" onClick={onCancel}>
          ← Back to dashboard
        </button>
      )}

      {hasExistingSetup && step === 0 && (
        <div className="panel wizard-panel">
          <h2>Existing setup found</h2>
          <p>
            {existingRuntimes.length} runtime(s) and {existingAgents.length} agent(s) are already configured.
          </p>
          <div className="wizard-actions">
            <button onClick={skipToDashboard}>Skip to dashboard</button>
            <button className="primary" onClick={() => setStep(0)}>
              Set up another LLM anyway
            </button>
          </div>
        </div>
      )}

      <div className="wizard-steps">
        {Array.from({ length: STEP_COUNT }).map((_, i) => (
          <div key={i} className={`wizard-step-dot ${i < step ? "done" : i === step ? "current" : ""}`} />
        ))}
      </div>

      {error && <div className="error-text">{error}</div>}

      {step === 0 && (
        <div className="panel wizard-panel">
          <h2>Find or install llama.cpp</h2>
          <p>We'll look for the llama-server binary on your machine first.</p>
          <button className="primary" disabled={detecting} onClick={handleDetect}>
            {detecting ? "Looking…" : "Detect llama.cpp"}
          </button>

          {pathVerified && executablePath && <p className="mono">✓ Verified: {executablePath}</p>}

          {guidance && !pathVerified && (
            <div className="panel wizard-code">
              {guidance.note}
              {"\n\n"}
              {guidance.releases_url}
            </div>
          )}

          <div>
            <div className="field-label">Or point us at it directly</div>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                style={{ flex: 1 }}
                placeholder="/path/to/llama-server"
                value={executablePath}
                onChange={(e) => handlePathEdited(e.target.value)}
              />
              <button disabled={verifying} onClick={() => setPicker("executable")}>
                Browse…
              </button>
              <button disabled={verifying || !executablePath} onClick={() => void handleVerifyPath()}>
                {verifying ? "Checking…" : "Verify"}
              </button>
            </div>
          </div>

          <div className="wizard-actions">
            <span />
            <button className="primary" disabled={!pathVerified} onClick={() => setStep(1)}>
              Continue
            </button>
          </div>
        </div>
      )}

      {step === 1 && (
        <div className="panel wizard-panel">
          <h2>Pick a model</h2>

          {existingModels.length > 0 && (
            <div>
              <div className="field-label">Already on your machine</div>
              <select
                value={selectedModelId ?? ""}
                onChange={(e) => setSelectedModelId(e.target.value ? Number(e.target.value) : null)}
              >
                <option value="">— choose —</option>
                {existingModels.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </div>
          )}

          <p>Don't have one yet? Search for it by name (even roughly) and download it:</p>
          <OnlineModelSearch onModelAdded={(id) => void handleModelAdded(id)} />

          <p>Already downloaded one? Point us at the folder it's in:</p>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              style={{ flex: 1 }}
              placeholder="/home/you/models"
              value={modelsFolder}
              onChange={(e) => setModelsFolder(e.target.value)}
            />
            <button disabled={scanning} onClick={() => setPicker("model-folder")}>
              Browse…
            </button>
            <button disabled={scanning || !modelsFolder} onClick={handleScan}>
              {scanning ? "Scanning…" : "Scan folder"}
            </button>
          </div>
          <div>
            <button disabled={scanning} onClick={() => setPicker("model-file")}>
              Or pick a single .gguf file…
            </button>
          </div>

          <div className="wizard-actions">
            <button onClick={() => setStep(0)}>Back</button>
            <button className="primary" disabled={selectedModelId == null} onClick={() => setStep(2)}>
              Continue
            </button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="panel wizard-panel">
          <h2>Create and start your first runtime</h2>
          <div>
            <div className="field-label">Name</div>
            <input style={{ width: "100%" }} value={runtimeName} onChange={(e) => setRuntimeName(e.target.value)} />
          </div>
          <div>
            <div className="field-label">Port</div>
            <input type="number" value={port} onChange={(e) => setPort(Number(e.target.value))} />
          </div>

          {runtimeId == null ? (
            <button className="primary" disabled={creatingRuntime} onClick={handleCreateAndStartRuntime}>
              {creatingRuntime ? "Starting…" : "Create & start"}
            </button>
          ) : (
            <p className="mono">
              Status: {runtimeStatus} {runtimeConfirmedOnline ? "— confirmed online ✓" : "— waiting…"}
            </p>
          )}

          <div className="wizard-actions">
            <button onClick={() => setStep(1)}>Back</button>
            <button className="primary" disabled={!runtimeConfirmedOnline} onClick={() => setStep(3)}>
              Continue
            </button>
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="panel wizard-panel">
          <h2>Attach an agent</h2>

          {existingAgents.length > 0 && (
            <div>
              <div className="field-label">Use an existing agent</div>
              <select
                value={useExistingAgentId}
                onChange={(e) =>
                  setUseExistingAgentId(e.target.value === "new" ? "new" : Number(e.target.value))
                }
              >
                <option value="new">— create a new agent —</option>
                {existingAgents.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </div>
          )}

          {useExistingAgentId === "new" && (
            <div>
              <div className="field-label">Name</div>
              <input style={{ width: "100%" }} value={agentName} onChange={(e) => setAgentName(e.target.value)} />

              <div className="field-label" style={{ marginTop: 8 }}>
                Backend
              </div>
              <select value={agentBackend} onChange={(e) => setAgentBackend(e.target.value)}>
                {agentBackends.map((b) => (
                  <option key={b.backend_id} value={b.backend_id} disabled={!b.detected && b.backend_id !== "generic"}>
                    {b.display_name} {b.backend_id !== "generic" && (b.detected ? "✓ detected" : "— not detected")}
                  </option>
                ))}
              </select>
              {agentBackend !== "generic" && (
                <p className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                  بعد از ساخت، از تب Chats می‌تونی ترمینال واقعی این ایجنت رو باز کنی — خودش خودکار به این runtime وصل
                  می‌شه.
                </p>
              )}
            </div>
          )}

          <div className="wizard-actions">
            <button onClick={() => setStep(2)}>Back</button>
            <button className="primary" disabled={finishing} onClick={handleFinish}>
              {finishing ? "Finishing…" : "Finish setup"}
            </button>
          </div>
        </div>
      )}

      {picker === "executable" && (
        <FileBrowserDialog
          title="Choose the llama-server executable"
          mode="file"
          executableOnly
          initialPath={executablePath || null}
          onSelect={(path) => {
            setPicker(null);
            setExecutablePath(path);
            void handleVerifyPath(path);
          }}
          onClose={() => setPicker(null)}
        />
      )}
      {picker === "model-folder" && (
        <FileBrowserDialog
          title="Choose a folder with .gguf models"
          mode="folder"
          initialPath={modelsFolder || null}
          onSelect={(path) => {
            setPicker(null);
            setModelsFolder(path);
          }}
          onClose={() => setPicker(null)}
        />
      )}
      {picker === "model-file" && (
        <FileBrowserDialog
          title="Choose a .gguf model file"
          mode="file"
          extensions={[".gguf"]}
          onSelect={(path) => void handlePickedModelPath(path)}
          onClose={() => setPicker(null)}
        />
      )}
    </div>
  );
}
