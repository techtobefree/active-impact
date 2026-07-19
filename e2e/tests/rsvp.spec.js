const { test, expect } = require('@playwright/test');
const { shot, expectNoGenericError, registerUI, uemail } = require('../helpers');

// A <input type="datetime-local"> value (viewer-local) offset from now by `days`.
// Computed (not hard-coded) so "future" stays future whenever the suite runs.
function dtLocal(days) {
  const d = new Date(Date.now() + days * 86400e3);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

async function createProject(page, title, startsLocal) {
  await page.getByRole('link', { name: /new project/i }).click();
  await page.locator('input[name=title]').fill(title);
  await page.locator('input[name=location_text]').fill('Riverside Park');
  await page.locator('input[name=starts_at]').fill(startsLocal);
  await page.getByRole('button', { name: /create project/i }).click();
  await expect(page.getByRole('heading', { name: title })).toBeVisible();
}

// A valid 1×1 PNG (resizeImage draws it onto a canvas, so it must decode).
const PNG_1x1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
  'base64',
);

test.describe('RSVP flow', () => {
  test('RSVP → check in → check out → back to check in, plus organizer leader toggle', async ({ page }, testInfo) => {
    await registerUI(page, uemail('rsvp'), 'password123', 'RSVP Organizer');
    await createProject(page, 'E2E RSVP Cleanup', dtLocal(7)); // future → live, never "over"

    // Nothing yet → the action is RSVP.
    const rsvp = page.getByRole('button', { name: /^rsvp$/i });
    await expect(rsvp).toBeVisible();
    await shot(page, testInfo, 'rsvp-available');
    await rsvp.click();

    // RSVP'd → "Check in" + a confirmation line + you appear in Who's coming.
    await expect(page.getByText(/You're RSVP'd/i)).toBeVisible();
    const checkin = page.getByRole('button', { name: /^check in$/i });
    await expect(checkin).toBeVisible();
    await expect(page.getByText(/Who's coming \(1\)/i)).toBeVisible();
    await shot(page, testInfo, 'rsvpd-whos-coming');
    await expectNoGenericError(page);

    // Organizer designates themselves an event leader — the flag must persist a reload.
    await page.locator('.switch').first().click();
    await expect(page.locator('.switch input[type=checkbox]').first()).toBeChecked();
    await shot(page, testInfo, 'leader-on');
    await page.reload();
    await expect(page.locator('.switch input[type=checkbox]').first()).toBeChecked();

    // Check in (self-service, no QR) → "Check out".
    await page.getByRole('button', { name: /^check in$/i }).click();
    const checkout = page.getByRole('button', { name: /^check out$/i });
    await expect(checkout).toBeVisible();
    await expect(page.getByText(/You're checked in/i)).toBeVisible();
    await shot(page, testInfo, 'checked-in');
    await expectNoGenericError(page);

    // Check out while still live → the action returns to "Check in" (RSVP persists).
    await checkout.click();
    await expect(page.getByRole('button', { name: /^check in$/i })).toBeVisible();
    await expect(page.getByText(/You're RSVP'd/i)).toBeVisible();
    await shot(page, testInfo, 'back-to-check-in');
    await expectNoGenericError(page);
  });

  test('an ended event offers no action — just an ended banner', async ({ page }, testInfo) => {
    await registerUI(page, uemail('over'), 'password123', 'Past Organizer');
    // The create form warns on past dates, so seed a already-ended project via the API.
    const id = await page.evaluate(async () => {
      const token = localStorage.getItem('ai_token');
      const startsAt = new Date(Date.now() - 4 * 3600e3).toISOString();
      const r = await fetch('/api/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
        body: JSON.stringify({ title: 'E2E Ended Event', location_text: 'Old Hall', starts_at: startsAt, expected_minutes: 60 }),
      });
      return (await r.json()).id;
    });

    await page.goto('/#/projects/' + id);
    await expect(page.getByRole('heading', { name: 'E2E Ended Event' })).toBeVisible();
    await expect(page.getByText(/has ended/i)).toBeVisible();
    await expect(page.getByRole('button', { name: /^rsvp$/i })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /^check in$/i })).toHaveCount(0);
    await shot(page, testInfo, 'ended');
    await expectNoGenericError(page);
  });

  test('event images: first upload becomes the cover, and the cover can be switched', async ({ page }, testInfo) => {
    await registerUI(page, uemail('img'), 'password123', 'Photo Organizer');
    await createProject(page, 'E2E Photo Project', dtLocal(7));

    const fileInput = page.locator('input[type=file]');

    // First photo → auto-primary → a cover renders with a ★ badge.
    await fileInput.setInputFiles({ name: 'before.png', mimeType: 'image/png', buffer: PNG_1x1 });
    await expect(page.locator('img.cover')).toBeVisible();
    await expect(page.locator('.primary-badge')).toHaveCount(1);
    await shot(page, testInfo, 'first-image-cover');
    await expectNoGenericError(page);

    // Second photo → two thumbs, still exactly one cover.
    await page.locator('input[type=file]').setInputFiles({ name: 'after.png', mimeType: 'image/png', buffer: PNG_1x1 });
    await expect(page.locator('.thumb')).toHaveCount(2);

    // Switch the cover to the other photo (the ☆ on the non-primary thumb).
    await page.locator('.thumb-star').first().click();
    await expect(page.locator('img.cover')).toBeVisible();
    await expect(page.locator('.primary-badge')).toHaveCount(1);
    await shot(page, testInfo, 'switched-cover');
    await expectNoGenericError(page);
  });
});
