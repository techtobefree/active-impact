const { test, expect } = require('@playwright/test');
const { shot, expectNoGenericError, registerUI, uemail, uname, findInFeed, dtLocal } = require('../helpers');

// A <input type="datetime-local"> value (viewer-local) offset from now by `days`.
// Computed (not hard-coded) so "future" stays future whenever the suite runs.
// POST /projects seeds the project AND its FIRST event, then lands on the detail.
// The projects list IS the home feed (FEED.md F2); #/projects is the legacy path.
async function createProject(page, title, startsLocal) {
  await page.goto('/#/projects');
  await page.getByRole('link', { name: /new service project/i }).click();
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
  test('RSVP → check in → check out → back to check in, plus the event-leader toggle', async ({ page }, testInfo) => {
    await registerUI(page, uemail('rsvp'), 'password123', 'RSVP Organizer');
    await createProject(page, 'E2E RSVP Cleanup ' + uname(), dtLocal(7)); // future → live, never "over"

    // The action now lives on the event row in the detail's Events section.
    // Nothing yet → RSVP.
    const rsvp = page.getByRole('button', { name: /^rsvp$/i });
    await expect(rsvp).toBeVisible();
    await shot(page, testInfo, 'rsvp-available');
    await rsvp.click();

    // RSVP'd → "Check in" (self-service, no QR).
    const checkin = page.getByRole('button', { name: /^check in$/i });
    await expect(checkin).toBeVisible();
    await shot(page, testInfo, 'rsvpd');
    await expectNoGenericError(page);

    // Check in → "Check out".
    await checkin.click();
    const checkout = page.getByRole('button', { name: /^check out$/i });
    await expect(checkout).toBeVisible();
    await shot(page, testInfo, 'checked-in');
    await expectNoGenericError(page);

    // Check out while still live → the action returns to "Check in" (RSVP persists).
    await checkout.click();
    await expect(page.getByRole('button', { name: /^check in$/i })).toBeVisible();
    await shot(page, testInfo, 'back-to-check-in');
    await expectNoGenericError(page);

    // "Who's coming" + the event-leader toggle are now on the EVENT lead hub
    // (per event), reached from the event row's Manage link.
    await page.getByRole('link', { name: /^manage$/i }).click();
    await expect(page).toHaveURL(/#\/events\/\d+\/lead$/);
    await expect(page.getByText(/Who's coming \(1\)/i)).toBeVisible();
    await shot(page, testInfo, 'whos-coming');

    // Organizer designates themselves an event leader — the flag persists a reload.
    await page.locator('.switch').first().click();
    await expect(page.locator('.switch input[type=checkbox]').first()).toBeChecked();
    await shot(page, testInfo, 'leader-on');
    await page.reload();
    await expect(page.locator('.switch input[type=checkbox]').first()).toBeChecked();
    await shot(page, testInfo, 'leader-persists');
    await expectNoGenericError(page);
  });

  test('act on an event straight from the feed card', async ({ page }, testInfo) => {
    await registerUI(page, uemail('feed'), 'password123', 'Feed Volunteer');
    const title = 'E2E Feed Action ' + uname(); // unique so the feed card is unambiguous across runs
    await createProject(page, title, dtLocal(7)); // future → live, never "over"

    // Back to the projects list (Upcoming).
    await page.goto('/#/projects');
    await expect(page).toHaveURL(/#\/projects$/);

    // Find the card and RSVP straight from the list (its embedded event action).
    await findInFeed(page, title);
    const card = page.locator('a.card', { hasText: title });
    await expect(card).toBeVisible();
    await card.getByRole('button', { name: /^rsvp$/i }).click();

    // The button must act in place: URL is STILL the list (did not open the
    // detail), and the card's button now reads "Check in".
    await expect(page).toHaveURL(/#\/projects$/);
    await expect(card.getByRole('button', { name: /^check in$/i })).toBeVisible();
    await shot(page, testInfo, 'feed-rsvpd');
    await expectNoGenericError(page);

    // Check in from the list → "Check out", still without leaving the list.
    await card.getByRole('button', { name: /^check in$/i }).click();
    await expect(page).toHaveURL(/#\/projects$/);
    await expect(card.getByRole('button', { name: /^check out$/i })).toBeVisible();
    await shot(page, testInfo, 'feed-checked-in');
    await expectNoGenericError(page);
  });

  test('a leader can add a second event; both appear on the project detail', async ({ page }, testInfo) => {
    await registerUI(page, uemail('addevt'), 'password123', 'Multi Event Organizer');
    const title = 'E2E Multi Event ' + uname();
    await createProject(page, title, dtLocal(7)); // seeds the first event

    // The detail lists exactly one event so far (each event row has a Manage link).
    await expect(page.getByText(/^Events$/)).toBeVisible();
    await expect(page.getByRole('link', { name: /^manage$/i })).toHaveCount(1);
    await shot(page, testInfo, 'one-event');

    // Open the "＋ Add event" form and schedule a second occurrence.
    await page.getByRole('button', { name: /add event/i }).click();
    await page.locator('input[name=location_text]').fill('North Gate');
    await page.locator('input[name=starts_at]').fill(dtLocal(14));
    await page.locator('form.add').getByRole('button', { name: /add event/i }).click();

    // Both events now appear (two Manage links) and the new location is listed.
    await expect(page.getByRole('link', { name: /^manage$/i })).toHaveCount(2);
    await expect(page.getByText(/North Gate/)).toBeVisible();
    await shot(page, testInfo, 'two-events');
    await expectNoGenericError(page);
  });

  test('an ended event offers no action — just an ended chip', async ({ page }, testInfo) => {
    await registerUI(page, uemail('over'), 'password123', 'Past Organizer');
    const title = 'E2E Ended Event ' + uname();
    // The create form warns on past dates, so seed an already-ended event via the API.
    const id = await page.evaluate(async (t) => {
      const token = localStorage.getItem('ai_token');
      const startsAt = new Date(Date.now() - 4 * 3600e3).toISOString();
      const r = await fetch('/api/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
        body: JSON.stringify({ title: t, location_text: 'Old Hall', starts_at: startsAt, expected_minutes: 60 }),
      });
      return (await r.json()).id;
    }, title);

    await page.goto('/#/projects/' + id);
    await expect(page.getByRole('heading', { name: title })).toBeVisible();
    // Over → no button on the event row, just an "Ended" chip.
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

    await page.goto('/#/projects');
    await expect(page).toHaveURL(/#\/projects$/);
    await findInFeed(page, title);
    // Upcoming (default) must NOT list an ended event.
    await expect(page.locator('a.card', { hasText: title })).toHaveCount(0);
    await shot(page, testInfo, 'upcoming-excludes-ended');

    // Past tab lists it.
    await page.getByRole('button', { name: /^past$/i }).click();
    await findInFeed(page, title);
    await expect(page.locator('a.card', { hasText: title })).toBeVisible();
    await shot(page, testInfo, 'past-includes-ended');
    await expectNoGenericError(page);
  });

  test('project images: first upload becomes the cover, and the cover can be switched', async ({ page }, testInfo) => {
    await registerUI(page, uemail('img'), 'password123', 'Photo Organizer');
    await createProject(page, 'E2E Photo Project ' + uname(), dtLocal(7));

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

  test('event images: a leader uploads a photo from the lead hub; it becomes the event cover', async ({ page }, testInfo) => {
    await registerUI(page, uemail('evimg'), 'password123', 'Event Photo Lead');
    const title = 'E2E Event Photo ' + uname();
    await createProject(page, title, dtLocal(7)); // seeds the first event

    // Into the event lead hub via the event row's Manage link.
    await page.getByRole('link', { name: /^manage$/i }).click();
    await expect(page).toHaveURL(/#\/events\/(\d+)\/lead$/);
    const eventId = page.url().match(/events\/(\d+)\/lead/)[1];

    // Upload a photo from the lead hub's Photos section.
    await expect(page.getByText(/^Photos$/)).toBeVisible();
    await page.locator('input[type=file]').setInputFiles({ name: 'event.png', mimeType: 'image/png', buffer: PNG_1x1 });

    // First photo auto-becomes the event cover → a ★ primary badge on the thumb.
    await expect(page.locator('.primary-badge')).toHaveCount(1);
    await shot(page, testInfo, 'event-photo-cover');
    await expectNoGenericError(page);

    // On the event detail, that event cover renders full-width.
    await page.goto(`/#/events/${eventId}`);
    await expect(page.locator('img.cover')).toBeVisible();
    await shot(page, testInfo, 'event-detail-cover');
    await expectNoGenericError(page);
  });
});
