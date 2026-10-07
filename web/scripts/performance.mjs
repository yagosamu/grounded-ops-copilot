import { spawn } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import { launch } from "chrome-launcher";
import lighthouse from "lighthouse";
import { chromium } from "@playwright/test";

const url = "http://127.0.0.1:3101";
const server = spawn(process.execPath, ["node_modules/next/dist/bin/next", "start", "--hostname", "127.0.0.1", "-p", "3101"], {
  cwd: process.cwd(), stdio: "ignore",
});
let chrome;

async function ready() {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1000) });
      if (response.ok) return;
    } catch (error) {
      if (attempt === 59) throw new Error("Production preview did not start", { cause: error });
    }
    await delay(500);
  }
  throw new Error("Production preview did not start");
}

try {
  await ready();
  chrome = await launch({
    chromePath: chromium.executablePath(),
    chromeFlags: process.env.CI ? ["--headless=new", "--no-sandbox"] : [],
  });
  const result = await lighthouse(url, {
    port: chrome.port, onlyCategories: ["performance"], logLevel: "error",
  });
  if (!result) throw new Error("Lighthouse returned no report");
  const lcp = result.lhr.audits["largest-contentful-paint"].numericValue;
  const cls = result.lhr.audits["cumulative-layout-shift"].numericValue;
  console.log(`Lighthouse preview: LCP=${Math.round(lcp)}ms CLS=${cls.toFixed(3)}`);
  if (lcp > 2500 || cls > 0.1) process.exitCode = 1;
} finally {
  if (chrome) await chrome.kill();
  server.kill("SIGTERM");
}
