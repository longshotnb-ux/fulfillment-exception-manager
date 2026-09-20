import { test, expect } from '@playwright/test';

const header = 'order_id,order_date,region,fulfillment_center,category,units,revenue,promised_days,actual_days,defect,returned';

async function importOrders(request, count, firstId, region) {
  const rows = Array.from({ length: count }, (_, i) => `${firstId + i},2026-05-01,${region},FC-TEST,Home,1,10,1,2,0,0`);
  const response = await request.post('/api/import', {
    data: `${header}\n${rows.join('\n')}`,
    headers: { 'Content-Type': 'text/csv' },
  });
  expect(response.ok()).toBeTruthy();
}

test('the demo walkthrough saves changes', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#result-count')).toHaveText('1–17 of 17 exceptions');
  if (process.env.UPDATE_PREVIEWS === '1') {
    await page.screenshot({ path: 'docs/exception-queue.png', fullPage: true });
  }
  await page.getByRole('searchbox', { name: 'Search exceptions' }).fill('1005');
  await expect(page.locator('#result-count')).toHaveText('1–1 of 1 exceptions');
  await page.getByRole('button', { name: 'Manage', exact: true }).click();
  await page.getByRole('combobox', { name: 'Workflow status' }).selectOption('Investigating');
  await page.getByRole('textbox', { name: 'Investigation notes' }).fill('Contacted the carrier; checking the delivery scan.');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await expect(page.locator('#exception-rows .status-pill')).toHaveText('Investigating');
  await page.getByRole('button', { name: 'Manage', exact: true }).click();
  await expect(page.locator('#history-list')).toContainText('Open → Investigating');
  await page.getByRole('combobox', { name: 'Workflow status' }).selectOption('Resolved');
  await page.getByRole('textbox', { name: 'Investigation notes' }).fill('Delivery confirmed; investigation complete.');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.locator('#revenue-risk')).toHaveText('$3,164.66');
  await page.getByRole('button', { name: 'Manage', exact: true }).click();
  await expect(page.locator('#history-status')).toHaveText('2 of 2 changes · newest first');
  if (process.env.UPDATE_PREVIEWS === '1') {
    await page.screenshot({ path: 'docs/change-history.png' });
  }
});

test('every page is reachable and filters restart at page one', async ({ page, request }) => {
  await importOrders(request, 51, 20000, 'Page test');
  await page.goto('/');
  await page.getByRole('combobox', { name: 'Filter by region' }).selectOption('Page test');
  await expect(page.locator('#result-count')).toHaveText('1–25 of 51 exceptions');
  const ids = await page.locator('#exception-rows tr td:first-child').allTextContents();
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(page.locator('#result-count')).toHaveText('26–50 of 51 exceptions');
  ids.push(...await page.locator('#exception-rows tr td:first-child').allTextContents());
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(page.locator('#result-count')).toHaveText('51–51 of 51 exceptions');
  ids.push(...await page.locator('#exception-rows tr td:first-child').allTextContents());
  expect(new Set(ids).size).toBe(51);
  await expect(page.getByRole('button', { name: 'Next', exact: true })).toBeDisabled();
  await page.getByRole('searchbox', { name: 'Search exceptions' }).fill('20000');
  await expect(page.locator('#result-count')).toHaveText('1–1 of 1 exceptions');
  await expect(page.locator('#page-count')).toHaveText('Page 1 of 1');
  await expect(page.getByRole('button', { name: 'Previous', exact: true })).toBeDisabled();
});

test('saving the last matching row on a page returns to an existing page', async ({ page, request }) => {
  await importOrders(request, 26, 30000, 'Resolve test');
  await page.goto('/');
  await page.getByRole('combobox', { name: 'Filter by region' }).selectOption('Resolve test');
  await page.getByRole('button', { name: /^Open / }).click();
  await expect(page.locator('#result-count')).toHaveText('1–25 of 26 exceptions');
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(page.locator('#result-count')).toHaveText('26–26 of 26 exceptions');
  await page.getByRole('button', { name: 'Manage', exact: true }).click();
  await page.getByRole('combobox', { name: 'Workflow status' }).selectOption('Resolved');
  await page.getByRole('textbox', { name: 'Investigation notes' }).fill('Carrier confirmed delivery.');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.locator('#result-count')).toHaveText('1–25 of 25 exceptions');
  await page.getByRole('button', { name: /^Resolved / }).click();
  await expect(page.locator('#result-count')).toHaveText('1–1 of 1 exceptions');
  await page.getByRole('button', { name: 'Manage', exact: true }).click();
  await expect(page.locator('#history-list')).toContainText('Open → Resolved');
  await expect(page.locator('#history-list')).toContainText('Carrier confirmed delivery.');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.reload();
  await page.getByRole('searchbox', { name: 'Search exceptions' }).fill('30000');
  await expect(page.locator('#result-count')).toHaveText('1–1 of 1 exceptions');
  await page.getByRole('button', { name: 'Manage', exact: true }).click();
  await expect(page.getByRole('combobox', { name: 'Workflow status' })).toHaveValue('Resolved');
  await expect(page.locator('#history-list')).toContainText('Open → Resolved');
});

test('a slow old filter response cannot overwrite the current queue', async ({ page, request }) => {
  await importOrders(request, 1, 40000, 'Race test');
  expect((await request.patch('/api/exceptions/40000', { data: { status: 'Resolved' } })).ok()).toBeTruthy();
  await page.goto('/');
  await page.getByRole('combobox', { name: 'Filter by region' }).selectOption('Race test');
  await expect(page.locator('#result-count')).toHaveText('1–1 of 1 exceptions');
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let started;
  const oldStarted = new Promise((resolve) => { started = resolve; });
  await page.route('**/api/exceptions?**', async (route) => {
    if (new URL(route.request().url()).searchParams.get('status') === 'Open') {
      const response = await route.fetch();
      started();
      await gate;
      await route.fulfill({ response });
    } else {
      await route.continue();
    }
  });
  await page.getByRole('button', { name: /^Open / }).click();
  await oldStarted;
  await page.getByRole('button', { name: /^Resolved / }).click();
  await expect(page.locator('#exception-rows .status-pill').first()).toHaveText('Resolved');
  const oldResponse = page.waitForResponse((response) => new URL(response.url()).searchParams.get('status') === 'Open');
  release();
  await (await oldResponse).finished();
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(page.locator('#exception-rows .status-pill').first()).toHaveText('Resolved');
  await expect(page.locator('#result-count')).toHaveText('1–1 of 1 exceptions');
});
