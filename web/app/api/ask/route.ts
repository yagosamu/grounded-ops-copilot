import { apiRequest, safeUpstreamError } from "@/lib/api";

export const runtime = "nodejs";

export async function POST(request: Request): Promise<Response> {
  const body = await request.json().catch(() => null) as { question?: unknown } | null;
  if (!body || typeof body.question !== "string" || body.question.trim().length < 1 || body.question.length > 1000) {
    return Response.json({ error: "invalid_question" }, { status: 400 });
  }
  const upstream = await apiRequest("/v1/ask", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: body.question }),
  });
  if (!upstream.ok) return safeUpstreamError(upstream);
  if (!upstream.body) return Response.json({ error: "empty_stream" }, { status: 502 });
  return new Response(upstream.body, {
    headers: { "Content-Type": "application/x-ndjson", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" },
  });
}
