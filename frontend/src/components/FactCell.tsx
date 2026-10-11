import type { KnowledgeFact } from "../api";
import { factLines, provenanceLabel, safeSourceUrl } from "../knowledge-utils";

/** One sourced fact in a table cell: the value, how we got it when it is not
 * straight from the primary source, and a link to where to check it. A fact the
 * source does not state says "Not published" instead of leaving a blank. */
export function FactCell({ fact }: { fact: KnowledgeFact | null }) {
  if (!fact) return <span className="fact-missing">—</span>;
  const lines = factLines(fact);
  const tag = provenanceLabel(fact.provenance);
  const link = safeSourceUrl(fact.source?.url);

  if (fact.provenance === "missing") {
    return (
      <span className="fact-missing" title={fact.note ?? undefined}>
        Not published
        {link && (
          <>
            {" "}
            <a className="fact-source" href={link} target="_blank" rel="noreferrer noopener" aria-label={`Check at ${fact.source?.label}`}>
              ↗
            </a>
          </>
        )}
      </span>
    );
  }

  return (
    <div className="fact-cell">
      {lines.length > 1 ? (
        <ul className="fact-list">
          {lines.map((l, i) => (
            <li key={i}>{l}</li>
          ))}
        </ul>
      ) : (
        <span className="fact-value">{lines[0]}</span>
      )}
      <div className="fact-meta">
        {tag && (
          <span className="fact-tag" data-provenance={fact.provenance} title={fact.note ?? undefined}>
            {tag}
          </span>
        )}
        {link && (
          <a className="fact-source" href={link} target="_blank" rel="noreferrer noopener" title={fact.source?.label}>
            {fact.source?.label} ↗
          </a>
        )}
      </div>
    </div>
  );
}
