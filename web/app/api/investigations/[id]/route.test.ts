import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const jar = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("next/headers", () => ({ cookies: async () => jar }));

import { GET } from "./route";

describe("investigation status proxy", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    process.env.API_BASE_URL = "http://127.0.0.1:8000";
  });
  afterEach(() => vi.unstubAllGlobals());

  it("rejects an unsafe identifier without calling the API", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const response = await GET(new Request("http://localhost/api/investigations/x"), { params: Promise.resolve({ id: "../feedback" }) });
    expect(response.status).toBe(400);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("forwards only an authenticated status lookup", async () => {
    jar.get.mockReturnValue({ value: "local-test-token" });
    const fetcher = vi.fn().mockResolvedValue(Response.json({ investigation_id: "inv-123", status: "running" }));
    vi.stubGlobal("fetch", fetcher);
    const response = await GET(new Request("http://localhost/api/investigations/inv-123"), { params: Promise.resolve({ id: "inv-123" }) });
    expect(response.status).toBe(200);
    expect(fetcher).toHaveBeenCalledWith("http://127.0.0.1:8000/v1/investigations/inv-123", expect.objectContaining({
      headers: { Authorization: "Bearer local-test-token" }, cache: "no-store",
    }));
  });
});
