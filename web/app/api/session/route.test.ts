import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const jar = vi.hoisted(() => ({
  get: vi.fn(), set: vi.fn(), delete: vi.fn(),
}));
vi.mock("next/headers", () => ({ cookies: async () => jar }));

import { GET, POST } from "./route";

describe("session boundary", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    process.env.API_BASE_URL = "http://127.0.0.1:8000";
  });
  afterEach(() => vi.unstubAllGlobals());

  it("never creates a session for a rejected token", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
    const response = await POST(new Request("http://localhost/api/session", {
      method: "POST", body: JSON.stringify({ token: "a".repeat(30) }),
    }));
    expect(response.status).toBe(401);
    expect(jar.set).not.toHaveBeenCalled();
  });

  it("sets an HttpOnly same-site cookie only after API validation", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ principal_id: "alice", tenant_id: "alpha" })));
    const token = "a".repeat(30);
    const response = await POST(new Request("http://localhost/api/session", {
      method: "POST", body: JSON.stringify({ token }),
    }));
    expect(response.status).toBe(200);
    expect(jar.set).toHaveBeenCalledWith("grounded_ops_session", token, expect.objectContaining({
      httpOnly: true, secure: false, sameSite: "strict", path: "/",
    }));
    expect(await response.text()).not.toContain(token);
  });

  it("does not call the API without a cookie", async () => {
    jar.get.mockReturnValue(undefined);
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    expect((await GET()).status).toBe(401);
    expect(fetcher).not.toHaveBeenCalled();
  });
});
