const { test, expect } = require('@playwright/test');
const { shot, expectNoGenericError, registerUI, uname, uemail, dtLocal } = require('../helpers');

// The project page's Invite button (SOCIAL.md §5b): the people you follow, or who
// follow you — and an invitation that actually arrives.

async function myId(page) {
  return page.evaluate(() => JSON.parse(localStorage.getItem('ai_user')).id);
}

async function makeProject(page, title) {
  return page.evaluate(async (t) => {
    const token = localStorage.getItem('ai_token');
    const p = await (await fetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
      body: JSON.stringify({
        title: t, location_text: 'Riverside Park',
        starts_at: new Date(Date.now() + 3 * 86400e3).toISOString(),
        expected_minutes: 120,
      }),
    })).json();
    return p.id;
  }, title);
}

test.describe('Inviting people', () => {
  test('invite someone from your follow graph and it reaches them', async ({ page, browser }, testInfo) => {
    await registerUI(page, uemail('host'), 'password123', 'Party Host');
    const hostId = await myId(page);

    // One person I follow, one who follows me — both should be invitable.
    const guests = [];
    for (const [name, direction] of [['Ana Fields', 'i-follow'], ['Ben Oduya', 'follows-me']]) {
      const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
      const guest = await ctx.newPage();
      await registerUI(guest, uemail('guest'), 'password123', name);
      const guestId = await myId(guest);
      if (direction === 'i-follow') {
        await page.goto(`/#/u/${guestId}`);
        await page.getByRole('button', { name: /^follow$/i }).click();
        await expect(page.getByRole('button', { name: /following/i })).toBeVisible();
      } else {
        await guest.goto(`/#/u/${hostId}`);
        await guest.getByRole('button', { name: /^follow$/i }).click();
        await expect(guest.getByRole('button', { name: /following/i })).toBeVisible();
      }
      guests.push({ name, ctx, page: guest });
    }

    // A stranger, following nobody, must never appear in the picker.
    const strangerCtx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const stranger = await strangerCtx.newPage();
    await registerUI(stranger, uemail('stranger'), 'password123', 'Passing Stranger');

    const title = 'E2E Invite Cleanup ' + uname();
    const projectId = await makeProject(page, title);
    await page.goto(`/#/projects/${projectId}`);

    // Invite opens the picker under the button.
    await page.getByRole('button', { name: /^invite$/i }).click();
    const picker = page.locator('.card', { hasText: 'Invite someone' });
    await expect(picker.locator('.card.row', { hasText: 'Ana Fields' })).toBeVisible();
    await expect(picker.locator('.card.row', { hasText: 'Ben Oduya' })).toBeVisible();
    await expect(picker.locator('.card.row', { hasText: 'Passing Stranger' })).toHaveCount(0);
    await shot(page, testInfo, 'picker');

    // One tap per person, and the button says so afterwards.
    await picker.locator('.card.row', { hasText: 'Ana Fields' })
      .getByRole('button', { name: /^invite$/i }).click();
    await expect(picker.locator('.card.row', { hasText: 'Ana Fields' })
      .getByRole('button', { name: /invited/i })).toBeVisible();
    await shot(page, testInfo, 'invited');
    await expectNoGenericError(page);

    // Ana hears about it: badge, notification, and a link to the project.
    const ana = guests[0].page;
    await ana.goto('/#/me');
    await expect(ana.locator('#bell')).toHaveClass(/has-unread/);
    await ana.locator('#bell').click();
    await expect(ana.getByText(/party host/i).first()).toBeVisible();
    await expect(ana.getByText(/invited you to/i)).toBeVisible();
    await shot(ana, testInfo, 'invitee-notified');

    await ana.getByRole('link', { name: title }).click();
    await expect(ana).toHaveURL(new RegExp(`#/projects/${projectId}$`));
    await expectNoGenericError(ana);

    // Ben was never invited, so he has nothing.
    const ben = guests[1].page;
    await ben.goto('/#/notifications');
    await expect(ben.getByText(/invited you to/i)).toHaveCount(0);

    for (const g of guests) await g.ctx.close();
    await strangerCtx.close();
  });

  test('re-opening the picker shows who is already invited', async ({ page, browser }, testInfo) => {
    await registerUI(page, uemail('host2'), 'password123', 'Second Host');
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const guest = await ctx.newPage();
    await registerUI(guest, uemail('g2'), 'password123', 'Invited Already');
    const guestId = await myId(guest);
    await page.goto(`/#/u/${guestId}`);
    await page.getByRole('button', { name: /^follow$/i }).click();
    await expect(page.getByRole('button', { name: /following/i })).toBeVisible();

    const projectId = await makeProject(page, 'E2E Repeat Invite ' + uname());
    await page.goto(`/#/projects/${projectId}`);
    await page.getByRole('button', { name: /^invite$/i }).click();
    const row = page.locator('.card.row', { hasText: 'Invited Already' });
    await row.getByRole('button', { name: /^invite$/i }).click();
    await expect(row.getByRole('button', { name: /invited/i })).toBeVisible();

    // Come back later: the state is remembered, not reset.
    await page.reload();
    await page.getByRole('button', { name: /^invite$/i }).click();
    await expect(page.locator('.card.row', { hasText: 'Invited Already' })
      .getByRole('button', { name: /invited/i })).toBeVisible();
    await shot(page, testInfo, 'already-invited');
    await expectNoGenericError(page);
    await ctx.close();
  });
});
