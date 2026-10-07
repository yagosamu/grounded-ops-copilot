import { cookies } from "next/headers";

export const COOKIE_NAME = "grounded_ops_session";

export function apiBaseUrl(): string {
  const raw = process.env.API_BASE_URL;
  if (!raw) throw new Error("API_BASE_URL is required");
  const url = new URL(raw);
  if (url.protocol !== "https:" && !(url.protocol === "http:" && ["127.0.0.1", "localhost"].includes(url.hostname))) {
    throw new Error("API_BASE_URL must use HTTPS outside localhost");
  }
  if (url.username || url.password || url.search || url.hash) throw new Error("Invalid API_BASE_URL");
  return url.origin;
}

export async function apiRequest(path: string, init: RequestInit = {}): Promise<Response> {
  const token = (await cookies()).get(COOKIE_NAME)?.value;
  if (!token) return Response.json({ error: "session_required" }, { status: 401 });
  try {
    return await fetch(`${apiBaseUrl()}${path}`, {
      ...init,
      headers: {
        ...init.headers,
        Authorization: `Bearer ${token}`,
      },
      cache: "no-store",
      signal: AbortSignal.timeout(15_000),
    });
  } catch {
    return Response.json({ error: "api_unavailable" }, { status: 503 });
  }
}

export function safeUpstreamError(response: Response): Response {
  const value = response.headers.get("x-correlation-id");
  const correlation_id = value && /^[0-9a-f-]{36}$/i.test(value) ? value : undefined;
  return Response.json(
    { error: response.status === 401 ? "session_expired" : "request_failed", correlation_id },
    { status: response.status },
  );
}
