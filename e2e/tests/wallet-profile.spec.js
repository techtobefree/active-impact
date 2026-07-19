const { test, expect } = require('@playwright/test');
const { shot, expectNoGenericError, registerUI, loginUI, uname } = require('../helpers');

test.describe('Wallet & profile', () => {
  test('a broke user tipping is told they lack tokens (not a generic error)', async ({ page }, testInfo) => {
    await registerUI(page, uname('broke'));
    await page.goto('/#/wallet');
    await expect(page.getByText(/your balance/i)).toBeVisible();
    await shot(page, testInfo, 'wallet-empty');

    await page.locator('input[name=to_username]').fill('ana');
    await page.locator('input[name=amount]').fill('1');
    await page.getByRole('button', { name: /send/i }).click();
    // The toast/error must explain the real reason.
    await expect(page.getByText(/not enough tokens|insufficient/i)).toBeVisible();
    await shot(page, testInfo, 'insufficient-tip');
  });

  test('a funded (seeded) user tips another; balance drops by exactly one', async ({ page }, testInfo) => {
    // Uses seed data — run `python scripts/seed.py`. Skips cleanly if absent/spent.
    await loginUI(page, 'ana', 'password123');
    await page.waitForTimeout(700);
    const signedIn = await page.locator('#nav').isVisible().catch(() => false);
    test.skip(!signedIn, 'seed data (ana) not present — run: python scripts/seed.py');

    await page.goto('/#/wallet');
    await expect(page.getByText(/your balance/i)).toBeVisible();
    const before = parseInt(((await page.locator('#balance').textContent()) || '').replace(/\D/g, ''), 10) || 0;
    test.skip(before < 1, 'seeded user ana has no tokens left — re-run: python scripts/seed.py');
    await shot(page, testInfo, 'wallet-ana');

    await page.locator('input[name=to_username]').fill('ben');
    await page.locator('input[name=amount]').fill('1');
    await page.locator('input[name=note]').fill('great cleanup');
    await page.getByRole('button', { name: /send/i }).click();

    // Proof the token actually MOVED (not just "ben appears somewhere"): the
    // topbar balance must drop by exactly one, and the ledger shows a 'tip'.
    await expect(page.locator('#balance')).toHaveText('🪙 ' + (before - 1));
    await expect(page.locator('.pill', { hasText: /^tip$/ }).first()).toBeVisible();
    await shot(page, testInfo, 'after-tip');
    await expectNoGenericError(page);
  });

  test('editing the profile display name persists', async ({ page }, testInfo) => {
    await registerUI(page, uname('prof'), 'password123', 'Original Name');
    await page.goto('/#/me');
    await shot(page, testInfo, 'me');
    await page.locator('input[name=display_name]').fill('Renamed Person');
    await page.getByRole('button', { name: /save changes/i }).click();
    await expect(page.getByText(/profile updated/i)).toBeVisible();
    await shot(page, testInfo, 'profile-saved');

    // Reflected on the public profile.
    await page.reload();
    await page.goto('/#/me');
    await expect(page.getByRole('heading', { name: /Renamed Person/i })).toBeVisible();
    await shot(page, testInfo, 'me-after');
    await expectNoGenericError(page);
  });
});
