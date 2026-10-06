import { useCallback, useEffect, useState } from "react";
import { api, apiErrorMessage, type ContextInspection } from "../api";
import {
  CONTEXT_STATUS_TEXT, CONTEXT_STATUS_TONE, WINDOW_SOURCE_TEXT, buildSegments, formatTokens, lastPromptNote,
} from "../context-utils";

interface Props {
  chatId: number;
  /** Changes whenever the conversation does, so the numbers follow it. */
  refreshKey: number;
}

/** Context Inspector: what would fill the model's window if this chat sent
 * its next request now, split into system prompt / tool definitions /
 * conversation / tool results. Collapsed by default -- it's a diagnostic,
 * not something to read every message -- but the one-line summary stays
 * visible so "getting full" is noticed without opening it. */
export function ContextInspectorPanel({ chatId, refreshKey }: Props) {
  const [data, setData] = useState<ContextInspection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api.getChatContext(chatId));
      setError(null);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not inspect the context."));
    }
  }, [chatId]);

  useEffect(() => {
    setData(null);
    void load();
  }, [load, refreshKey]);

  if (error) {
    return (
      <div className="context-panel">
        <span className="error-text" role="alert">
          {error}
        </span>
      </div>
    );
  }
  if (!data) return null;

  const segments = buildSegments(data.categories, data.context_window);
  const tone = CONTEXT_STATUS_TONE[data.status];
  const note = lastPromptNote(data.total_tokens, data.last_prompt_tokens);

  return (
    <div className="context-panel" data-status={data.status}>
      <button className="context-summary" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span>Context</span>
        <span className="mono">
          {formatTokens(data.total_tokens)}
          {data.context_window ? ` / ${formatTokens(data.context_window)}` : ""} tokens
          {data.percent_used != null && ` (${data.percent_used}%)`}
        </span>
        <span className="context-status" data-tone={tone}>
          {CONTEXT_STATUS_TEXT[data.status]}
        </span>
      </button>

      {open && (
        <div className="context-details">
          {segments.length === 0 ? (
            <div className="muted">Nothing in the context yet.</div>
          ) : (
            <div className="context-bar" role="img" aria-label="Context usage by category">
              {segments.map((s) => (
                <div
                  key={s.key}
                  className={`context-segment context-segment-${s.key}`}
                  style={{ width: `${s.widthPercent}%` }}
                  title={`${s.label}: ${formatTokens(s.tokens)} tokens`}
                />
              ))}
            </div>
          )}

          <ul className="context-legend">
            {data.categories.map((c) => (
              <li key={c.key} data-testid={`context-${c.key}`}>
                <span className={`context-swatch context-segment-${c.key}`} />
                <span>{c.label}</span>
                <span className="mono">{formatTokens(c.tokens)}</span>
                <span className="muted">
                  {c.key === "tool_definitions" ? `${data.tool_count} tools` : `${c.items} item${c.items === 1 ? "" : "s"}`}
                  {c.percent_of_window != null && ` · ${c.percent_of_window}%`}
                </span>
              </li>
            ))}
          </ul>

          <p className="muted context-meta">
            {data.method === "tokenizer"
              ? "Counted with the runtime's own tokenizer."
              : "Estimated from text length (the runtime is offline or couldn't tokenize) — real counts may differ, especially for non-English text."}
            {data.context_window_source && ` Window: ${WINDOW_SOURCE_TEXT[data.context_window_source]}.`}
            {data.headroom_tokens != null &&
              (data.headroom_tokens >= 0
                ? ` ${formatTokens(data.headroom_tokens)} tokens of headroom.`
                : ` Over by ${formatTokens(-data.headroom_tokens)} tokens.`)}
            {note && ` ${note[0].toUpperCase()}${note.slice(1)}.`}
          </p>

          {data.largest_items.length > 0 && (
            <div>
              <div className="runtime-metric-label">LARGEST ITEMS</div>
              <ul className="context-largest">
                {data.largest_items.map((i) => (
                  <li key={i.message_id}>
                    <span className="mcp-tag">{i.role}</span>
                    <span className="mono">~{formatTokens(i.estimated_tokens)}</span>
                    <span className="muted context-preview">{i.preview || "(empty)"}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
