const { test, expect } = require('@playwright/test');
const { shot, expectNoGenericError, registerUI, uname, uemail } = require('../helpers');

test.describe('Projects', () => {
  test('create a project, open the event lead hub, self check-in via the waiver, and check out', async ({ page }, testInfo) => {
    await registerUI(page, uemail('proj'), 'password123', 'Project Host');
    await shot(page, testInfo, 'home');

    // The projects list IS the home feed again (FEED.md F2); #/projects still works.
    await page.goto('/#/projects');

    // Create a service project (POST /projects seeds its first event; expected_minutes defaults to 120).
    const title = 'E2E Beach Cleanup ' + uname();
    await page.getByRole('link', { name: /new service project/i }).click();
    await page.locator('input[name=title]').fill(title);
    await page.locator('textarea[name=description]').fill('Bring gloves and sunscreen.');
    await page.locator('input[name=location_text]').fill('Sunset Beach, north lot');
    await page.locator('input[name=starts_at]').fill('2026-08-01T10:00');
    await shot(page, testInfo, 'new-project-filled');
    await page.getByRole('button', { name: /create project/i }).click();

    await expect(page.getByRole('heading', { name: title })).toBeVisible();
    await shot(page, testInfo, 'project-detail');
    await expectNoGenericError(page);

    // The lead/QR flow is now PER-EVENT — reach it from the event row's Manage link.
    await page.getByRole('link', { name: /^manage$/i }).click();
    await expect(page).toHaveURL(/#\/events\/\d+\/lead$/);
    await expect(page.getByRole('img', { name: /qr/i })).toBeVisible();
    await shot(page, testInfo, 'event-lead-qr');

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
    await page.goto('/#/projects');
    const title = 'My Mine Project ' + uname();
    await page.getByRole('link', { name: /new service project/i }).click();
    await page.locator('input[name=title]').fill(title);
    await page.locator('input[name=location_text]').fill('Somewhere');
    await page.locator('input[name=starts_at]').fill('2026-08-02T09:00');
    await page.getByRole('button', { name: /create project/i }).click();
    await expect(page.getByRole('heading', { name: title })).toBeVisible();

    await page.goto('/#/projects');
    await page.getByRole('button', { name: /^mine$/i }).click();
    await expect(page.getByRole('link', { name: title })).toBeVisible();
    await shot(page, testInfo, 'mine-tab');
    await expectNoGenericError(page);
  });
});
