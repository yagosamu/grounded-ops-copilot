export type Citation = {
  evidence_id: string;
  document_version_id: string;
  span: [number, number];
  resolved: boolean;
};

export type Source = {
  evidence_id: string;
  source_id: string;
  document_id: string;
  document_version_id: string;
  span: [number, number];
};

export type AskEvent =
  | { type: "status"; status: "retrieving" | "generating" }
  | { type: "delta"; status: "unverified"; text: string }
  | {
      type: "final";
      status: "verified" | "abstained" | "failed" | "degraded";
      answer_id?: string;
      answer: string | null;
      claims: { text: string; citations: Citation[] }[];
      sources: Source[];
      abstention?: string | null;
      error?: { code: string; message: string };
    };

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

export function parseAskLine(line: string): AskEvent | null {
  let value: unknown;
  try {
    value = JSON.parse(line);
  } catch {
    return null;
  }
  if (!isObject(value)) return null;
  if (value.type === "status" && (value.status === "retrieving" || value.status === "generating")) {
    return value as AskEvent;
  }
  if (value.type === "delta" && value.status === "unverified" && typeof value.text === "string") {
    return value as AskEvent;
  }
  if (
    value.type === "final" &&
    ["verified", "abstained", "failed", "degraded"].includes(String(value.status)) &&
    (typeof value.answer === "string" || value.answer === null) &&
    Array.isArray(value.claims) &&
    Array.isArray(value.sources)
  ) {
    if (value.status === "verified" && (typeof value.answer_id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(value.answer_id))) return null;
    if (value.status === "verified" && (value.claims.length === 0 || value.sources.length === 0)) return null;
    if (value.status === "verified" && !value.claims.every(
      (claim: unknown) => isObject(claim) && typeof claim.text === "string" &&
        Array.isArray(claim.citations) && claim.citations.length > 0 && claim.citations.every(
          (citation: unknown) => isObject(citation) && citation.resolved === true &&
            typeof citation.evidence_id === "string" && typeof citation.document_version_id === "string" &&
            Array.isArray(citation.span) && citation.span.length === 2,
        ),
    )) return null;
    return value as AskEvent;
  }
  return null;
}

export async function* readAskStream(stream: ReadableStream<Uint8Array>): AsyncGenerator<AskEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let terminal = false;
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let newline = buffer.indexOf("\n");
      while (newline >= 0) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (line) {
          const event = parseAskLine(line);
          if (!event) throw new Error("Invalid Ask event");
          if (terminal) throw new Error("Event after terminal Ask event");
          terminal = event.type === "final";
          yield event;
        }
        newline = buffer.indexOf("\n");
      }
      if (done) break;
      if (buffer.length > 1_000_000) throw new Error("Ask event exceeds size limit");
    }
    if (buffer.trim()) throw new Error("Incomplete Ask event");
    if (!terminal) throw new Error("Missing terminal Ask event");
  } finally {
    reader.releaseLock();
  }
}
