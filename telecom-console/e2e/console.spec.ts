import { test, expect } from "@playwright/test";

test("dashboard loads and shows navigation", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Operations overview" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Monitoring" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Auditing" })).toBeVisible();
  await expect(page.getByRole("link", { name: "AI Agents" })).toBeVisible();
});

test("agents page shows MCP config", async ({ page }) => {
  await page.goto("/agents");
  await expect(page.getByRole("heading", { name: "AI agent connections" })).toBeVisible();
  await expect(page.getByText("telecom-mcp-tools")).toBeVisible();
});
