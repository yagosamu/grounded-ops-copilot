import { apiRequest, safeUpstreamError } from "@/lib/api";

export async function GET(_request: Request, context: { params: Promise<{ id: string }> }): Promise<Response> {
  const { id } = await context.params;
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(id)) {
    return Response.json({ error: "invalid_investigation_id" }, { status: 400 });
  }
  const upstream = await apiRequest(`/v1/investigations/${encodeURIComponent(id)}`);
  if (!upstream.ok) return safeUpstreamError(upstream);
  return Response.json(await upstream.json(), { headers: { "Cache-Control": "no-store" } });
}
