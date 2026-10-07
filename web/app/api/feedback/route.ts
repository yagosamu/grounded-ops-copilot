import { apiRequest, safeUpstreamError } from "@/lib/api";

export async function POST(request: Request): Promise<Response> {
  const body = await request.json().catch(() => null) as { answer_id?: unknown; rating?: unknown } | null;
  if (!body || typeof body.answer_id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(body.answer_id) || !["helpful", "not_helpful"].includes(String(body.rating))) {
    return Response.json({ error: "invalid_feedback" }, { status: 400 });
  }
  const upstream = await apiRequest("/v1/feedback", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!upstream.ok) return safeUpstreamError(upstream);
  return Response.json(await upstream.json(), { status: 201 });
}
