import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const source = {
  evidence_id: "chunk-alpha-001", source_id: "runbooks", document_id: "recovery-runbook",
  document_version_id: "version-2026-10", span: [20, 140],
};

async function session(page: Page) {
  await page.route("**/api/session", async (route) => {
    if (route.request().method() === "DELETE") return route.fulfill({ json: { signed_out: true } });
    return route.fulfill({ json: { principal_id: "alice", tenant_id: "alpha" } });
  });
}

test("verified Ask exposes citation navigation and records feedback", async ({ page }) => {
  await session(page);
  await page.route("**/api/ask", (route) => route.fulfill({
    status: 200, contentType: "application/x-ndjson",
    body: [
      { type: "status", status: "retrieving" },
      { type: "delta", status: "unverified", text: "Unchecked draft" },
      { type: "final", status: "verified", answer_id: "answer-123", answer: "Restart the worker.",
        claims: [{ text: "Restart the worker.", citations: [{ evidence_id: source.evidence_id, document_version_id: source.document_version_id, span: source.span, resolved: true }] }],
        sources: [source] },
    ].map((item) => JSON.stringify(item)).join("\n") + "\n",
  }));
  await page.route("**/api/evidence?*", (route) => route.fulfill({ json: {
    evidence: [{ ...source, chunk_id: source.evidence_id, text: "Restart the worker after checking queue depth.", is_current: true, source_timestamp: "2026-10-01T00:00:00Z" }],
  } }));
  let feedback: unknown = null;
  await page.route("**/api/feedback", async (route) => {
    feedback = route.request().postDataJSON();
    await route.fulfill({ status: 201, json: { recorded: true } });
  });

  await page.goto("/");
  await page.getByLabel("Your question").fill("How do we recover the worker?");
  await page.getByRole("button", { name: /Ask GroundedOps/ }).click();
  await expect(page.getByText("Restart the worker.").first()).toBeVisible();
  await expect(page.getByText("Unchecked draft")).toHaveCount(0);
  await page.getByRole("button", { name: /chunk-alpha/ }).click();
  await expect(page.locator(".source-card:focus")).toContainText("recovery-runbook");
  await expect(page.getByText("Restart the worker after checking queue depth.")).toBeVisible();
  await page.getByRole("button", { name: "Yes" }).click();
  await expect(page.getByText("Feedback recorded. Thank you.")).toBeVisible();
  expect(feedback).toMatchObject({ answer_id: "answer-123", rating: "helpful" });
});

test("abstention never shows a verified answer", async ({ page }) => {
  await session(page);
  await page.route("**/api/ask", (route) => route.fulfill({
    contentType: "application/x-ndjson",
    body: JSON.stringify({ type: "final", status: "abstained", answer: "Insufficient evidence", claims: [], sources: [], abstention: "no_evidence" }) + "\n",
  }));
  await page.goto("/");
  await page.getByLabel("Your question").fill("Unknown procedure?");
  await page.getByRole("button", { name: /Ask GroundedOps/ }).click();
  await expect(page.getByRole("heading", { name: "Not enough evidence" })).toBeVisible();
  await expect(page.getByText("Insufficient evidence")).toBeVisible();
  await expect(page.getByRole("button", { name: "Yes" })).toHaveCount(0);
});

test("failed and degraded Ask do not promote a draft", async ({ page }) => {
  await session(page);
  let attempt = 0;
  await page.route("**/api/ask", (route) => {
    attempt += 1;
    const final = attempt === 1
      ? { type: "final", status: "failed", answer: null, claims: [], sources: [], error: { code: "retrieval_unavailable", message: "retrieval temporarily unavailable" } }
      : { type: "final", status: "degraded", answer: null, claims: [], sources: [source], error: { code: "generation_unavailable", message: "generation temporarily unavailable" } };
    return route.fulfill({ contentType: "application/x-ndjson", body: JSON.stringify(final) + "\n" });
  });
  await page.goto("/");
  await page.getByLabel("Your question").fill("What failed?");
  await page.getByRole("button", { name: /Ask GroundedOps/ }).click();
  await expect(page.getByRole("heading", { name: "Could not answer" })).toBeVisible();
  await page.getByRole("button", { name: /Ask GroundedOps/ }).click();
  await expect(page.getByRole("heading", { name: "Evidence-only mode" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Yes" })).toHaveCount(0);
});

test("login is keyboard-operable and has no serious axe violations", async ({ page }) => {
  await page.route("**/api/session", (route) => {
    if (route.request().method() === "GET") return route.fulfill({ status: 401, json: { error: "session_required" } });
    return route.fulfill({ json: { principal_id: "alice", tenant_id: "alpha" } });
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Evidence before answers." })).toBeVisible();
  const loginAxe = await new AxeBuilder({ page }).analyze();
  expect(loginAxe.violations.filter((item) => ["critical", "serious"].includes(item.impact ?? ""))).toEqual([]);
  await page.getByLabel("API access token").fill("eyJaaaaaaaaaaaaaaaaaaaaaa.bbbbbbbbbbbbbbbbbbbbbbbb.cccccccccccccccccccc");
  await page.getByLabel("API access token").press("Tab");
  await expect(page.getByRole("button", { name: /Start local session/ })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: /Ask with confidence/ })).toBeVisible();
  const appAxe = await new AxeBuilder({ page }).analyze();
  expect(appAxe.violations.filter((item) => ["critical", "serious"].includes(item.impact ?? ""))).toEqual([]);
});

test("workspace remains usable on a narrow viewport", async ({ page }) => {
  await session(page);
  await page.setViewportSize({ width: 375, height: 720 });
  await page.goto("/");
  await expect(page.getByLabel("Your question")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});

test("expired API session returns to the login form", async ({ page }) => {
  await session(page);
  await page.route("**/api/ask", (route) => route.fulfill({ status: 401, json: { error: "session_expired" } }));
  await page.goto("/");
  await page.getByLabel("Your question").fill("Can I still ask?");
  await page.getByRole("button", { name: /Ask GroundedOps/ }).click();
  await expect(page.getByRole("heading", { name: "Evidence before answers." })).toBeVisible();
});

test("investigation status is readable without enabling a new agent run", async ({ page }) => {
  await session(page);
  await page.route("**/api/investigations/inv-123", (route) => route.fulfill({ json: {
    investigation_id: "inv-123", status: "running", updated_at: "2026-10-07T12:00:00Z",
    stop_reason: null, report_available: false,
    progress: { steps: 2, tokens_used: 140, tool_calls: 1, retrieval_attempts: 1 },
  } }));
  await page.goto("/");
  await expect(page.getByText("NEW RUNS DISABLED")).toBeVisible();
  await page.getByLabel("Investigation ID").fill("inv-123");
  await page.getByRole("button", { name: "Check status" }).click();
  await expect(page.getByRole("status").filter({ hasText: "inv-123" })).toContainText("Steps 2 · tools 1 · tokens 140");
  await expect(page.getByRole("button", { name: /Start investigation/ })).toHaveCount(0);
});

test("unavailable investigation status is not reported as running", async ({ page }) => {
  await session(page);
  await page.route("**/api/investigations/inv-missing", (route) => route.fulfill({ status: 404, json: { error: "request_failed" } }));
  await page.goto("/");
  await page.getByLabel("Investigation ID").fill("inv-missing");
  await page.getByRole("button", { name: "Check status" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Investigation not found" })).toContainText("not found or not enabled");
  await expect(page.getByRole("status").filter({ hasText: "inv-missing" })).toHaveCount(0);
});
