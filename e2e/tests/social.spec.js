const { test, expect } = require('@playwright/test');
const { shot, expectNoGenericError, registerUI, uname, uemail, dtLocal } = require('../helpers');

// The social layer (SOCIAL.md) as a person meets it: follow someone, watch what
// they do turn up in the Following tab and on their page, block a follower
// without losing them, and get a badge when the people you follow turn up.

// A live event, created through the API with the page's own session.
async function liveEvent(page, title) {
  return page.evaluate(async (t) => {
    const token = localStorage.getItem('ai_token');
    const p = await (await fetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
      body: JSON.stringify({
        title: t, location_text: 'Riverside Park, boathouse',
        starts_at: new Date(Date.now() - 20 * 60 * 1000).toISOString(),
        expected_minutes: 180,
      }),
    })).json();
    return { projectId: p.id, eventId: p.events[0].id };
  }, title);
}

async function myId(page) {
  return page.evaluate(() => JSON.parse(localStorage.getItem('ai_user')).id);
}

// Open home's Following tab, freshly. goto('/#/') is a no-op when you are already
// there (and the tab click then short-circuits on the unchanged scope), so this
// always reloads first — the state it is about was usually changed elsewhere.
async function openFollowing(page) {
  await page.goto('/#/');
  await page.reload();
  await page.getByRole('button', { name: /^following$/i }).click();
  // The tab must own what is under it: a slower projects request that started
  // first must not paint project cards here (caught on production latency).
  await expect(page.locator('#view a.card[href^="#/projects/"]')).toHaveCount(0);
  // …and the search box stays put rather than vanishing under the tap.
  await expect(page.locator('#view input[type=search]')).toBeVisible();
}

test.describe('Following people', () => {
  test('follow someone and their activity reaches my feed and their page', async ({ page, browser }, testInfo) => {
    // Ana turns up at an event and logs nothing else.
    const anaCtx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const ana = await anaCtx.newPage();
    await registerUI(ana, uemail('ana'), 'password123', 'Ana Fields');
    const anaId = await myId(ana);
    const title = 'E2E Social Cleanup ' + uname();
    const { eventId } = await liveEvent(ana, title);
    await ana.goto(`/#/events/${eventId}`);
    await ana.getByRole('button', { name: /^rsvp$/i }).click();
    await ana.getByRole('button', { name: /^check in$/i }).click();
    await expect(ana.getByText(/self-reported/i)).toBeVisible();

    // I open her page: her information at the top, then what she has been doing.
    await registerUI(page, uemail('me'), 'password123', 'Follower One');
    await page.goto(`/#/u/${anaId}`);
    await expect(page.getByRole('heading', { name: 'Ana Fields' })).toBeVisible();
    await expect(page.getByText(/checked in at/i)).toBeVisible();
    await shot(page, testInfo, 'their-page');

    // Before following, my Following tab is empty.
    await openFollowing(page);
    await expect(page.getByText(/follow them to see what they do/i)).toBeVisible();

    // Follow her, and the same activity is now in my feed.
    await page.goto(`/#/u/${anaId}`);
    await page.getByRole('button', { name: /^follow$/i }).click();
    await expect(page.getByRole('button', { name: /following/i })).toBeVisible();
    await openFollowing(page);
    await expect(page.locator('.card.activity', { hasText: 'Ana Fields' }).first()).toBeVisible();
    await shot(page, testInfo, 'following-tab');
    await expectNoGenericError(page);
    await anaCtx.close();
  });

  test("an organizer's page shows what they organized, and what is next", async ({ page, browser }, testInfo) => {
    // The bug this covers: you tap the creator of a project and their page is
    // blank, because organizing was not activity and nothing was back-filled.
    const anaCtx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const ana = await anaCtx.newPage();
    await registerUI(ana, uemail('org'), 'password123', 'Ana Organizer');
    const anaId = await myId(ana);
    const title = 'E2E Organized ' + uname();
    const { projectId, eventId } = await liveEvent(ana, title);
    await ana.goto(`/#/events/${eventId}`);
    await ana.getByRole('button', { name: /^rsvp$/i }).click();

    await registerUI(page, uemail('looker'), 'password123', 'Looker');
    await page.goto(`/#/u/${anaId}`);

    // Current information first — where they are going, under the buttons.
    await expect(page.getByText(/now & next/i)).toBeVisible();
    await expect(page.locator('.card', { hasText: title }).first()).toBeVisible();

    // Then a separated section with their history, INCLUDING starting the project.
    await expect(page.getByText(/^activity$/i)).toBeVisible();
    await expect(page.locator('.card.activity', { hasText: /started/i }).first()).toBeVisible();
    await shot(page, testInfo, 'organizer-page');
    await expectNoGenericError(page);
    await anaCtx.close();
  });

  test('the search box stays on the Following tab, and searches it', async ({ page, browser }, testInfo) => {
    await registerUI(page, uemail('searcher'), 'password123', 'Searcher');

    // Two people I follow, doing things at differently-named projects.
    for (const [name, project] of [['Ana Fields', 'Riverside Cleanup'], ['Ben Oduya', 'Food Bank Sorting']]) {
      const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
      const other = await ctx.newPage();
      await registerUI(other, uemail('doer'), 'password123', name);
      const otherId = await myId(other);
      await liveEvent(other, project + ' ' + uname());
      await page.goto(`/#/u/${otherId}`);
      await page.getByRole('button', { name: /^follow$/i }).click();
      await expect(page.getByRole('button', { name: /following/i })).toBeVisible();
      await ctx.close();
    }

    await openFollowing(page);
    const search = page.locator('#view input[type=search]');
    await expect(search).toBeVisible();                       // it did NOT disappear
    await expect(search).toHaveAttribute('placeholder', /people and projects/i);
    await expect(page.locator('.card.activity')).toHaveCount(2);
    await shot(page, testInfo, 'following-with-search');

    // Search by person…
    await search.fill('Oduya');
    await expect(page.locator('.card.activity', { hasText: 'Ben Oduya' })).toHaveCount(1);
    await expect(page.locator('.card.activity', { hasText: 'Ana Fields' })).toHaveCount(0);

    // …and by project.
    await search.fill('Riverside');
    await expect(page.locator('.card.activity', { hasText: 'Ana Fields' })).toHaveCount(1);
    await shot(page, testInfo, 'searched');
    await expectNoGenericError(page);
  });

  test('a blocked follower stays a follower and stops seeing me', async ({ page, browser }, testInfo) => {
    // I do something worth seeing.
    await registerUI(page, uemail('blocker'), 'password123', 'Blocker');
    const meId = await myId(page);
    const title = 'E2E Block Cleanup ' + uname();
    const { eventId } = await liveEvent(page, title);
    await page.goto(`/#/events/${eventId}`);
    await page.getByRole('button', { name: /^rsvp$/i }).click();

    // Ben follows me and can see it.
    const benCtx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const ben = await benCtx.newPage();
    await registerUI(ben, uemail('ben'), 'password123', 'Ben Oduya');
    await ben.goto(`/#/u/${meId}`);
    await ben.getByRole('button', { name: /^follow$/i }).click();
    await openFollowing(ben);
    await expect(ben.locator('.card.activity', { hasText: 'Blocker' }).first()).toBeVisible();
    await shot(ben, testInfo, 'ben-sees-me');

    // I block him from my own followers list.
    await page.goto(`/#/u/${meId}/followers`);
    const row = page.locator('.card.row', { hasText: 'Ben Oduya' });
    await expect(row).toBeVisible();
    page.once('dialog', (d) => d.accept());
    await row.getByRole('button', { name: /^block$/i }).click();
    // He is STILL a follower — the founder's exact ask — just blocked.
    await expect(row.getByRole('button', { name: /^unblock$/i })).toBeVisible();
    await expect(page.locator('.card.row', { hasText: 'Ben Oduya' })).toHaveCount(1);
    await shot(page, testInfo, 'blocked-but-still-a-follower');

    // And now he sees nothing of mine, on either surface.
    await openFollowing(ben);
    await expect(ben.locator('.card.activity', { hasText: 'Blocker' })).toHaveCount(0);
    await ben.goto(`/#/u/${meId}`);
    await expect(ben.getByText(/nothing from blocker yet/i)).toBeVisible();
    await shot(ben, testInfo, 'ben-sees-nothing');

    // Unblocking gives it all back.
    await page.goto(`/#/u/${meId}/followers`);
    const backRow = page.locator('.card.row', { hasText: 'Ben Oduya' });
    await backRow.getByRole('button', { name: /^unblock$/i }).click();
    // The button flipping back is the proof the DELETE actually landed — without
    // it, a failure below is ambiguous between "unblock broke" and "reads broke".
    await expect(backRow.getByRole('button', { name: /^block$/i })).toBeVisible();
    await openFollowing(ben);
    await expect(ben.locator('.card.activity', { hasText: 'Blocker' }).first()).toBeVisible();
    await expectNoGenericError(ben);
    await benCtx.close();
  });

  test('followers/following expand in place, switch, and collapse again', async ({ page, browser }, testInfo) => {
    await registerUI(page, uemail('tabs'), 'password123', 'Tab Owner');
    const meId = await myId(page);

    // Two people follow me; I follow one of them.
    for (const name of ['Fan One', 'Fan Two']) {
      const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
      const fan = await ctx.newPage();
      await registerUI(fan, uemail('fan'), 'password123', name);
      await fan.goto(`/#/u/${meId}`);
      await fan.getByRole('button', { name: /^follow$/i }).click();
      if (name === 'Fan One') {
        const fanId = await myId(fan);
        await page.goto(`/#/u/${fanId}`);
        await page.getByRole('button', { name: /^follow$/i }).click();
      }
      await ctx.close();
    }

    await page.goto('/#/me');
    await page.reload();
    const card = page.locator('.follow-card');
    const panel = card.locator('.follow-panel');
    const rows = panel.locator('.card.row');

    // Collapsed to start: just the two tabs.
    await expect(card.getByRole('button', { name: /followers/i })).toBeVisible();
    await expect(panel).toBeHidden();
    await shot(page, testInfo, 'collapsed');

    // Tap Followers -> the list drops out of the same card.
    await card.getByRole('button', { name: /followers/i }).click();
    await expect(panel).toBeVisible();
    await expect(rows).toHaveCount(2);
    await shot(page, testInfo, 'followers-open');

    // Tap Following -> switches without closing.
    await card.getByRole('button', { name: /following/i }).click();
    await expect(panel).toBeVisible();
    await expect(rows).toHaveCount(1);
    await shot(page, testInfo, 'following-open');

    // Tap the open one again -> back to just the tabs.
    await card.getByRole('button', { name: /following/i }).click();
    await expect(panel).toBeHidden();
    await expectNoGenericError(page);
  });

  test('past 100, the card hands over to the full page', async ({ page }, testInfo) => {
    await registerUI(page, uemail('many'), 'password123', 'Popular');
    const meId = await myId(page);

    // A crowd is expensive to register for real, so the crowd is faked at the
    // boundary being tested: exactly one page of rows, and a count beyond it.
    await page.route(`**/api/users/${meId}/followers*`, async (route) => {
      const body = Array.from({ length: 100 }, (_, i) => ({
        id: 9000 + i, display_name: `Crowd ${i}`, is_guest: false,
        is_following: false, is_blocked: false,
      }));
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    });
    await page.evaluate(() => {
      const u = JSON.parse(localStorage.getItem('ai_user'));
      u.follower_count = 137;
      localStorage.setItem('ai_user', JSON.stringify(u));
    });

    await page.goto('/#/me');
    const card = page.locator('.follow-card');
    await card.getByRole('button', { name: /followers/i }).click();
    await expect(card.locator('.follow-panel .card.row')).toHaveCount(100);
    const seeAll = card.getByRole('link', { name: /see all 137/i });
    await expect(seeAll).toBeVisible();
    await shot(page, testInfo, 'see-all');

    await seeAll.click();
    await expect(page).toHaveURL(new RegExp(`#/u/${meId}/followers$`));
    // The full page is the detailed one: it sorts.
    await expect(page.getByRole('button', { name: /^name$/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /^recent$/i })).toBeVisible();
    await shot(page, testInfo, 'full-page');
    await expectNoGenericError(page);
  });

  test('the bell counts what people I follow do, and opening it clears the badge', async ({ page, browser }, testInfo) => {
    await registerUI(page, uemail('watcher'), 'password123', 'Watcher');

    const anaCtx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const ana = await anaCtx.newPage();
    await registerUI(ana, uemail('doer'), 'password123', 'Doer');
    const anaId = await myId(ana);
    const { eventId } = await liveEvent(ana, 'E2E Bell Cleanup ' + uname());

    await page.goto(`/#/u/${anaId}`);
    await page.getByRole('button', { name: /^follow$/i }).click();

    // She RSVPs — that is exactly what the founder wanted to be told about.
    await ana.goto(`/#/events/${eventId}`);
    await ana.getByRole('button', { name: /^rsvp$/i }).click();
    // Wait for it to LAND (the action becomes Check in) — clicking only dispatches
    // the request, and the bell below reads a count the server has to have written.
    await expect(ana.getByRole('button', { name: /^check in$/i })).toBeVisible();

    await page.goto('/#/me');                       // any navigation refreshes the bell
    const bell = page.locator('#bell');
    await expect(bell).toHaveClass(/has-unread/);
    await expect(bell).toHaveAttribute('data-count', '1');
    await shot(page, testInfo, 'bell-unread');

    await bell.click();
    await expect(page).toHaveURL(/#\/notifications$/);
    await expect(page.locator('.card.activity', { hasText: 'Doer' }).first()).toBeVisible();
    await expect(bell).not.toHaveClass(/has-unread/);
    await shot(page, testInfo, 'notifications');
    await expectNoGenericError(page);
    await anaCtx.close();
  });
});
