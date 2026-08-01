const { test, expect } = require('@playwright/test');
const { shot, expectNoGenericError, uname, uemail } = require('../helpers');

// A valid 1×1 PNG (resizeImage draws it onto a canvas, so it must decode).
const PNG_1x1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
  'base64',
);

// Fill the log screen (photo + caption) and submit. Where it LANDS is the point:
// an attached post goes to its event's feed, an unattached one to its own page.
async function postRecord(page, caption) {
  await page.locator('input[type=file]').setInputFiles({ name: 'act.png', mimeType: 'image/png', buffer: PNG_1x1 });
  await page.locator('#view textarea').fill(caption);
  await page.getByRole('button', { name: /^post$/i }).click();
}

// A project whose only event is HAPPENING NOW, so a logged photo can attach to
// it. Created through the API with the page's own session (the UI's create form
// is covered by projects.spec.js).
async function liveProject(page, title) {
  return page.evaluate(async (t) => {
    const token = localStorage.getItem('ai_token');
    const r = await fetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
      body: JSON.stringify({
        title: t,
        location_text: 'Riverside Park',
        starts_at: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
        expected_minutes: 180,
      }),
    });
    const p = await r.json();
    return { id: p.id, eventId: p.events[0].id };
  }, title);
}

test.describe('Service log on events (the one feed)', () => {
  test('log to an event: it lands on that event and rides the project card home', async ({ page }, testInfo) => {
    await page.goto('/');
    await expect(page).toHaveURL(/#\/log$/);           // first run: brand-new guest
    const title = 'Live Cleanup ' + uname('proj');
    const { id, eventId } = await liveProject(page, title);

    // "＋ Log to this event" from the event page states the target outright.
    await page.goto(`/#/events/${eventId}`);
    await page.getByRole('link', { name: /log to this event/i }).click();
    await expect(page).toHaveURL(new RegExp(`#/log/${eventId}$`));
    await expect(page.locator('.target')).toContainText(title);  // "Posting to <project>"
    await shot(page, testInfo, 'posting-to-event');

    const caption = 'Six bags off the east bank ' + uname('cap');
    await postRecord(page, caption);

    // It lands on the event's own feed, newest first.
    await expect(page).toHaveURL(new RegExp(`#/events/${eventId}$`));
    await expect(page.locator('article.record').first()).toContainText(caption);
    await shot(page, testInfo, 'event-feed');
    await expectNoGenericError(page);

    // …and the SAME photo now rides on the project's card in the home feed —
    // the merge, end to end (FEED.md F2/F3).
    await page.goto('/#/');
    const card = page.locator('#view .card', { hasText: title }).first();
    await expect(card.locator('.record-strip')).toBeVisible();
    await expect(card.locator('.record-mini-cap').first()).toContainText(caption);
    await shot(page, testInfo, 'card-carries-the-photo');
    await expectNoGenericError(page);
  });

  test('the check-in signal attaches a photo with no GPS and no picking', async ({ page }, testInfo) => {
    await page.goto('/');
    await expect(page).toHaveURL(/#\/log$/);
    const title = 'Checked In ' + uname('ci');
    const { eventId } = await liveProject(page, title);

    // Check in, then log from the plain #/log screen: the server works out where
    // I am from the open participation alone (FEED.md §4 — no location prompt).
    await page.evaluate(async (ev) => {
      await fetch(`/api/events/${ev}/checkin`, {
        method: 'POST', headers: { Authorization: 'Bearer ' + localStorage.getItem('ai_token') },
      });
    }, eventId);

    await page.goto('/#/');
    await page.locator('#fab-log').click();
    await expect(page).toHaveURL(/#\/log$/);
    await expect(page.locator('.target')).toContainText(title);
    await shot(page, testInfo, 'auto-matched-by-checkin');

    const caption = 'Cleared the underpass ' + uname('cap');
    await postRecord(page, caption);
    await expect(page).toHaveURL(new RegExp(`#/events/${eventId}$`));
    await expect(page.locator('article.record').first()).toContainText(caption);
    await expectNoGenericError(page);
  });

  test('nothing to match: the photo is still saved, and can be attached after', async ({ page }, testInfo) => {
    await page.goto('/');
    await expect(page).toHaveURL(/#\/log$/);
    await expect(page.locator('.target')).toContainText(/not linked to an event/i);

    const caption = 'Helped a neighbour ' + uname('loose');
    await postRecord(page, caption);

    // Lands on its own page, saved, with a way to give it a home (F7/F8).
    await expect(page).toHaveURL(/#\/r\/\d+$/);
    await expect(page.locator('article.record')).toContainText(caption);
    await expect(page.getByRole('button', { name: /attach to an event/i })).toBeVisible();
    await shot(page, testInfo, 'unattached-record');
    await expectNoGenericError(page);

    // It lives on my own page meanwhile.
    await page.goto('/#/me');
    await expect(page.locator('article.record', { hasText: caption })).toHaveCount(1);

    // Now make an event live and attach it: the record moves onto that event.
    const title = 'Attach Target ' + uname('att');
    const { eventId } = await liveProject(page, title);
    await page.goBack();
    await page.reload();
    await page.getByRole('button', { name: /attach to an event/i }).click();
    await page.locator('.picker-item', { hasText: title }).click();
    await expect(page).toHaveURL(new RegExp(`#/events/${eventId}$`));
    await expect(page.locator('article.record').first()).toContainText(caption);
    await shot(page, testInfo, 'attached-after-the-fact');
    await expectNoGenericError(page);
  });

  test('a second guest sees it, cheers it; converting keeps it mine; then delete', async ({ page }, testInfo) => {
    await page.goto('/');
    await expect(page).toHaveURL(/#\/log$/);
    const title = 'Shared Cleanup ' + uname('shared');
    const { eventId } = await liveProject(page, title);

    // Post BEFORE the target lookup can answer. The screen says "Log to this
    // event", so it must attach to it regardless — the route is the truth, not
    // the lookup that merely prints the project's name. (Real latency caught
    // this; the delay makes it deterministic.)
    // times: 1 — only the log screen's lookup is slowed; the event page that
    // follows loads normally (and an outliving handler would fail to continue).
    await page.route(`**/api/events/${eventId}`, async (route) => {
      await new Promise((r) => setTimeout(r, 3000));
      await route.continue().catch(() => {});
    }, { times: 1 });
    await page.goto(`/#/log/${eventId}`);
    const caption = 'Planted trees ' + uname('two');
    await postRecord(page, caption);
    await expect(page).toHaveURL(new RegExp(`#/events/${eventId}$`));

    // Become a brand-new guest: clear the session at the root and re-boot (the
    // first-run redirect to #/log only fires there).
    await page.goto('/#/');
    await page.evaluate(() => localStorage.clear());
    await page.reload();
    await expect(page).toHaveURL(/#\/log$/); // fresh guest → first-run Log (boot finished)

    // The event's public feed shows the first guest's record; this one can cheer.
    await page.goto(`/#/events/${eventId}`);
    const card = page.locator('article.record', { hasText: caption }).first();
    await expect(card).toBeVisible();
    const cheer = card.getByRole('button', { name: /cheer/i });
    await cheer.click();
    await expect(cheer).toContainText('1');
    await shot(page, testInfo, 'second-guest-cheered');
    await expectNoGenericError(page);

    // A third identity logs its own record, converts, and still owns it.
    await page.goto('/#/');
    await page.evaluate(() => localStorage.clear());
    await page.reload();
    await expect(page).toHaveURL(/#\/log$/);
    const mine = 'Cooked meals ' + uname('mine');
    await page.goto(`/#/log/${eventId}`);
    await postRecord(page, mine);
    await expect(page).toHaveURL(new RegExp(`#/events/${eventId}$`));

    await page.goto('/#/me');
    await page.locator('input[name=email]').fill(uemail('conv'));
    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page.getByText(/account saved/i)).toBeVisible();
    await shot(page, testInfo, 'converted');
    await expectNoGenericError(page);

    // Still mine → the ⋯ menu offers Delete, and deleting removes it.
    await page.goto(`/#/events/${eventId}`);
    const myCard = page.locator('article.record', { hasText: mine }).first();
    await expect(myCard).toBeVisible();
    await myCard.getByRole('button', { name: /more/i }).click();
    page.once('dialog', (d) => d.accept()); // the delete confirm
    await myCard.getByRole('button', { name: /delete/i }).click();
    await expect(page.locator('article.record', { hasText: mine })).toHaveCount(0);
    await shot(page, testInfo, 'deleted');
    await expectNoGenericError(page);
  });
});
