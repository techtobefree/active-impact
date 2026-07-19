const { test, expect } = require('@playwright/test');
const { shot, expectNoGenericError, registerUI, uname, uemail } = require('../helpers');

test.describe('Projects', () => {
  test('create a project, show the QR, self check-in via the waiver, and check out', async ({ page }, testInfo) => {
    await registerUI(page, uemail('proj'), 'password123', 'Project Host');
    await shot(page, testInfo, 'home');

    // Create a project (expected_minutes defaults to 120).
    await page.getByRole('link', { name: /new project/i }).click();
    await page.locator('input[name=title]').fill('E2E Beach Cleanup');
    await page.locator('textarea[name=description]').fill('Bring gloves and sunscreen.');
    await page.locator('input[name=location_text]').fill('Sunset Beach, north lot');
    await page.locator('input[name=starts_at]').fill('2026-08-01T10:00');
    await shot(page, testInfo, 'new-project-filled');
    await page.getByRole('button', { name: /create project/i }).click();

    await expect(page.getByRole('heading', { name: 'E2E Beach Cleanup' })).toBeVisible();
    await shot(page, testInfo, 'project-detail');
    await expectNoGenericError(page);

    // Lead screen — the QR must render.
    await page.getByRole('link', { name: /lead screen/i }).click();
    await expect(page.getByRole('img', { name: /qr/i })).toBeVisible();
    await shot(page, testInfo, 'lead-qr');

    // Check in yourself → waiver → agree.
    await page.getByRole('link', { name: /check in yourself/i }).click();
    await expect(page.getByRole('button', { name: /i agree/i })).toBeVisible();
    await expect(page.getByText('Volunteer waiver')).toBeVisible();
    await shot(page, testInfo, 'waiver');
    await page.getByRole('button', { name: /i agree/i }).click();
    await expect(page.getByText(/checked in/i)).toBeVisible();
    await shot(page, testInfo, 'checked-in');
    await expectNoGenericError(page);

    // Check out (immediate → ~0 tokens, but the flow must complete cleanly).
    await page.getByRole('button', { name: /check out/i }).click();
    await expect(page.getByText(/checked out/i).first()).toBeVisible();
    await shot(page, testInfo, 'checked-out');
    await expectNoGenericError(page);
  });

  test('a newly created project appears under "Mine"', async ({ page }, testInfo) => {
    await registerUI(page, uemail('mine'), 'password123', 'Mine Tester');
    await page.getByRole('link', { name: /new project/i }).click();
    await page.locator('input[name=title]').fill('My Mine Project');
    await page.locator('input[name=location_text]').fill('Somewhere');
    await page.locator('input[name=starts_at]').fill('2026-08-02T09:00');
    await page.getByRole('button', { name: /create project/i }).click();
    await expect(page.getByRole('heading', { name: 'My Mine Project' })).toBeVisible();

    await page.goto('/#/');
    await page.getByRole('button', { name: /^mine$/i }).click();
    await expect(page.getByRole('link', { name: /My Mine Project/i })).toBeVisible();
    await shot(page, testInfo, 'mine-tab');
    await expectNoGenericError(page);
  });
});
