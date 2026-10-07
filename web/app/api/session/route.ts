import { cookies } from "next/headers";
import { apiBaseUrl, COOKIE_NAME } from "@/lib/api";

export const runtime = "nodejs";

export async function GET(): Promise<Response> {
  const token = (await cookies()).get(COOKIE_NAME)?.value;
  if (!token) return Response.json({ error: "session_required" }, { status: 401 });
  try {
    const upstream = await fetch(`${apiBaseUrl()}/v1/session`, {
      headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: AbortSignal.timeout(5000),
    });
    if (!upstream.ok) return Response.json({ error: "session_expired" }, { status: 401 });
    return Response.json(await upstream.json(), { headers: { "Cache-Control": "no-store" } });
  } catch {
    return Response.json({ error: "api_unavailable" }, { status: 503 });
  }
}

export async function POST(request: Request): Promise<Response> {
  const { token } = await request.json().catch(() => ({ token: null })) as { token?: unknown };
  if (typeof token !== "string" || token.length < 20 || token.length > 3800) {
    return Response.json({ error: "invalid_token" }, { status: 400 });
  }
  try {
    const upstream = await fetch(`${apiBaseUrl()}/v1/session`, {
      headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: AbortSignal.timeout(5000),
    });
    if (!upstream.ok) return Response.json({ error: "invalid_token" }, { status: 401 });
    const hostname = new URL(request.url).hostname;
    (await cookies()).set(COOKIE_NAME, token, {
      httpOnly: true, secure: !["localhost", "127.0.0.1"].includes(hostname), sameSite: "strict", path: "/", maxAge: 60 * 30,
    });
    return Response.json(await upstream.json(), { headers: { "Cache-Control": "no-store" } });
  } catch {
    return Response.json({ error: "api_unavailable" }, { status: 503 });
  }
}

export async function DELETE(): Promise<Response> {
  (await cookies()).delete(COOKIE_NAME);
  return Response.json({ signed_out: true });
}
