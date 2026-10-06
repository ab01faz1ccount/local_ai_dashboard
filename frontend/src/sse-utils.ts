/**
 * frontend/src/sse-utils.ts
 *
 * An incremental Server-Sent Events parser. `fetch` hands the response
 * body over in arbitrary chunks that need not line up with event
 * boundaries (an event can be split across two chunks, or several can
 * arrive in one), so parsing has to be stateful. Deliberately only the
 * subset this app's backend emits -- `event:` + one `data:` line per
 * event -- but tolerant of CRLF, comment lines and unknown fields.
 */

export interface SseEvent {
  event: string;
  data: string;
}

export interface SseParser {
  /** Feed the next decoded chunk; returns every event it completed. */
  feed(chunk: string): SseEvent[];
}

export function createSseParser(): SseParser {
  let buffer = "";

  function parseFrame(frame: string): SseEvent | null {
    let event = "message";
    const data: string[] = [];
    for (const rawLine of frame.split("\n")) {
      const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
      if (line === "" || line.startsWith(":")) continue; // blank / comment (keep-alive)
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
    return data.length === 0 ? null : { event, data: data.join("\n") };
  }

  return {
    feed(chunk: string): SseEvent[] {
      buffer += chunk;
      const out: SseEvent[] = [];
      // an event ends at a blank line (LF or CRLF flavored)
      for (;;) {
        const m = /\r?\n\r?\n/.exec(buffer);
        if (!m) break;
        const frame = buffer.slice(0, m.index);
        buffer = buffer.slice(m.index + m[0].length);
        const ev = parseFrame(frame);
        if (ev) out.push(ev);
      }
      return out;
    },
  };
}
