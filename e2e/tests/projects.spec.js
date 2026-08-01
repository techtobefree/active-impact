const { test, expect } = require('@playwright/test');
const { shot, expectNoGenericError, registerUI, uname, uemail, findInFeed, dtLocal } = require('../helpers');

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
    await page.locator('input[name=starts_at]').fill(dtLocal(1));
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

  test('an address typed once is offered back the next time', async ({ page }, testInfo) => {
    // LOCATIONS.md: every address becomes a location, and the venues already in
    // use are one tap away — no "add a location" step anywhere.
    await registerUI(page, uemail('loc'), 'password123', 'Location Tester');
    const venue = 'Maple Hall, ' + uname('venue');
    await page.goto('/#/projects/new');
    await page.locator('input[name=title]').fill('Venue Seeder ' + uname());
    await page.locator('input[name=location_text]').fill(venue);
    await page.locator('input[name=starts_at]').fill(dtLocal(30));
    await page.getByRole('button', { name: /create project/i }).click();
    await expect(page.getByRole('heading', { name: /venue seeder/i })).toBeVisible();

    // A second project: typing part of it offers the whole thing back.
    await page.goto('/#/projects/new');
    const input = page.locator('input[name=location_text]');
    await input.fill('maple');
    const listId = await input.getAttribute('list');
    expect(listId, 'the address field is wired to a datalist').toBeTruthy();
    await expect(page.locator(`datalist#${listId} option[value="${venue}"]`)).toHaveCount(1);
    await shot(page, testInfo, 'address-suggested');
    await expectNoGenericError(page);
  });

  test('a newly created project appears under "Mine"', async ({ page }, testInfo) => {
    await registerUI(page, uemail('mine'), 'password123', 'Mine Tester');
    await page.goto('/#/projects');
    const title = 'My Mine Project ' + uname();
    await page.getByRole('link', { name: /new service project/i }).click();
    await page.locator('input[name=title]').fill(title);
    await page.locator('input[name=location_text]').fill('Somewhere');
    await page.locator('input[name=starts_at]').fill(dtLocal(2));
    await page.getByRole('button', { name: /create project/i }).click();
    await expect(page.getByRole('heading', { name: title })).toBeVisible();

    await page.goto('/#/projects');
    await page.getByRole('button', { name: /^mine$/i }).click();
    await findInFeed(page, title);
    await expect(page.getByRole('link', { name: title })).toBeVisible();
    await shot(page, testInfo, 'mine-tab');
    await expectNoGenericError(page);
  });
});
