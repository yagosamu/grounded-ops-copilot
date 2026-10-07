import { describe, expect, it } from "vitest";
import { parseAskLine, readAskStream } from "./ask";

describe("Ask stream", () => {
  it("accepts only a verified terminal answer as grounded", () => {
    expect(parseAskLine('{"type":"delta","status":"unverified","text":"draft"}')).toEqual({
      type: "delta", status: "unverified", text: "draft",
    });
    expect(parseAskLine('{"type":"final","status":"verified","answer_id":"answer-123","answer":"ok","claims":[{"text":"ok","citations":[{"evidence_id":"e1","document_version_id":"v1","span":[0,2],"resolved":true}]}],"sources":[{"evidence_id":"e1","source_id":"s1","document_id":"d1","document_version_id":"v1","span":[0,2]}]}')?.type).toBe("final");
    expect(parseAskLine('{"type":"final","status":"verified","answer_id":"answer-123","answer":"ok","claims":[],"sources":[]}')).toBeNull();
    expect(parseAskLine('{"type":"final","status":"verified","answer":"ok","claims":"bad"}')).toBeNull();
  });

  it("parses NDJSON across arbitrary network chunks", async () => {
    const source = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('{"type":"status","status":"retr'));
        controller.enqueue(new TextEncoder().encode('ieving"}\n{"type":"final","status":"abstained","answer":"No evidence","claims":[],"sources":[]}\n'));
        controller.close();
      },
    });
    const events = [];
    for await (const event of readAskStream(source)) events.push(event);
    expect(events.map((event) => event.type)).toEqual(["status", "final"]);
  });

  it("rejects a stream without a terminal event", async () => {
    const source = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('{"type":"delta","status":"unverified","text":"draft"}\n'));
        controller.close();
      },
    });
    const events = [];
    await expect(async () => {
      for await (const event of readAskStream(source)) events.push(event);
    }).rejects.toThrow("terminal");
  });
});
