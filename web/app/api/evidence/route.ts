import { apiRequest, safeUpstreamError } from "@/lib/api";

export async function GET(request: Request): Promise<Response> {
  const query = new URL(request.url).searchParams.get("q")?.trim();
  if (!query || query.length > 1000) return Response.json({ error: "invalid_query" }, { status: 400 });
  const upstream = await apiRequest(`/v1/evidence/search?q=${encodeURIComponent(query)}&limit=20`);
  if (!upstream.ok) return safeUpstreamError(upstream);
  return Response.json(await upstream.json(), { headers: { "Cache-Control": "no-store" } });
}
