const { test, expect } = require('@playwright/test');
const { shot, expectNoGenericError, registerUI, uemail, uname } = require('../helpers');

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

    // RSVP'd → "Check in" (the action sits to the right of the details) + you
    // appear in Who's coming.
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
    await shot(page, testInfo, 'checked-in');
    await expectNoGenericError(page);

    // Check out while still live → the action returns to "Check in" (RSVP persists).
    await checkout.click();
    await expect(page.getByRole('button', { name: /^check in$/i })).toBeVisible();
    await shot(page, testInfo, 'back-to-check-in');
    await expectNoGenericError(page);
  });

  test('act on a project straight from the events list', async ({ page }, testInfo) => {
    await registerUI(page, uemail('feed'), 'password123', 'Feed Volunteer');
    const title = 'E2E Feed Action ' + uname(); // unique so the feed card is unambiguous across runs
    await createProject(page, title, dtLocal(7)); // future → live, never "over"

    // Back to the events list (Upcoming).
    await page.goto('/#/');
    await expect(page).toHaveURL(/#\/$/);

    // Find the card and RSVP straight from the feed.
    const card = page.locator('a.card', { hasText: title });
    await expect(card).toBeVisible();
    await card.getByRole('button', { name: /^rsvp$/i }).click();

    // The button must act in place: URL is STILL the list (did not open the
    // detail), and the card's button now reads "Check in".
    await expect(page).toHaveURL(/#\/$/);
    await expect(card.getByRole('button', { name: /^check in$/i })).toBeVisible();
    await shot(page, testInfo, 'list-rsvpd');
    await expectNoGenericError(page);

    // Check in from the feed → "Check out", still without leaving the list.
    await card.getByRole('button', { name: /^check in$/i }).click();
    await expect(page).toHaveURL(/#\/$/);
    await expect(card.getByRole('button', { name: /^check out$/i })).toBeVisible();
    await shot(page, testInfo, 'list-checked-in');
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
    // Over → no button, just an "Ended" chip to the right of the details.
    await expect(page.getByText(/^Ended$/i)).toBeVisible();
    await expect(page.getByRole('button', { name: /^rsvp$/i })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /^check in$/i })).toHaveCount(0);
    await shot(page, testInfo, 'ended');
    await expectNoGenericError(page);
  });

  test('follow a service project', async ({ page }, testInfo) => {
    await registerUI(page, uemail('follow'), 'password123', 'Follow Volunteer');
    const title = 'E2E Follow Project ' + uname(); // unique so the follower count is unambiguous
    await createProject(page, title, dtLocal(7)); // future → live, never "over"

    // Follow controls sit under the head, open to every signed-in viewer.
    // Broad matcher so the same button survives the "Follow" ⇄ "✓ Following" flip.
    const followBtn = page.getByRole('button', { name: /^(follow|✓ following)$/i });
    await expect(followBtn).toHaveText(/^follow$/i);
    await expect(page.getByText(/^0 followers$/i)).toBeVisible();

    // Follow → button flips to "✓ Following" and the count increments in place.
    await followBtn.click();
    await expect(followBtn).toHaveText(/following/i);
    await expect(page.getByText(/^1 follower$/i)).toBeVisible();
    await shot(page, testInfo, 'following');
    await expectNoGenericError(page);

    // Unfollow → back to "Follow" and 0 followers.
    await followBtn.click();
    await expect(followBtn).toHaveText(/^follow$/i);
    await expect(page.getByText(/^0 followers$/i)).toBeVisible();
    await shot(page, testInfo, 'unfollowed');
    await expectNoGenericError(page);
  });

  test('an ended event lands under Past, not Upcoming', async ({ page }, testInfo) => {
    await registerUI(page, uemail('pasttab'), 'password123', 'Past Tabs Organizer');
    const title = 'E2E Ended Tab ' + uname(); // unique so tab membership is unambiguous
    // Backdated via the API (the create form refuses past dates).
    await page.evaluate(async (t) => {
      const token = localStorage.getItem('ai_token');
      const startsAt = new Date(Date.now() - 4 * 3600e3).toISOString();
      await fetch('/api/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
        body: JSON.stringify({ title: t, location_text: 'Old Hall', starts_at: startsAt, expected_minutes: 60 }),
      });
    }, title);

    await page.goto('/#/');
    await expect(page).toHaveURL(/#\/$/);
    // Upcoming (default) must NOT list an ended event.
    await expect(page.locator('a.card', { hasText: title })).toHaveCount(0);
    await shot(page, testInfo, 'upcoming-excludes-ended');

    // Past tab lists it.
    await page.getByRole('button', { name: /^past$/i }).click();
    await expect(page.locator('a.card', { hasText: title })).toBeVisible();
    await shot(page, testInfo, 'past-includes-ended');
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
