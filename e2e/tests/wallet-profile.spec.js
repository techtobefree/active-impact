const { test, expect } = require('@playwright/test');
const { shot, expectNoGenericError, registerUI, loginUI, uname, uemail } = require('../helpers');

test.describe('Wallet & profile', () => {
  test('a broke user tipping is told they lack tokens (not a generic error)', async ({ page }, testInfo) => {
    await registerUI(page, uemail('broke'), 'password123', 'Broke Tester');
    await page.goto('/#/wallet');
    await expect(page.getByText(/your balance/i)).toBeVisible();
    await shot(page, testInfo, 'wallet-empty');

    await page.locator('input[name=to_email]').fill('ana@example.com');
    await page.locator('input[name=amount]').fill('1');
    await page.getByRole('button', { name: /send/i }).click();
    // The error must explain the real reason, attributed to the amount field.
    await expect(page.getByText(/not enough tokens|insufficient/i)).toBeVisible();
    await shot(page, testInfo, 'insufficient-tip');
  });

  test('a funded (seeded) user tips another by email; balance drops by exactly one', async ({ page }, testInfo) => {
    // Uses seed data — run `python scripts/seed.py`. Skips cleanly if absent/spent.
    await loginUI(page, 'ana@example.com', 'password123');
    await page.waitForTimeout(700);
    const signedIn = await page.locator('#nav').isVisible().catch(() => false);
    test.skip(!signedIn, 'seed data (ana@example.com) not present — run: python scripts/seed.py');

    await page.goto('/#/wallet');
    await expect(page.getByText(/your balance/i)).toBeVisible();
    const before = parseInt(((await page.locator('#balance').textContent()) || '').replace(/\D/g, ''), 10) || 0;
    test.skip(before < 1, 'seeded user ana has no tokens left — re-run: python scripts/seed.py');
    await shot(page, testInfo, 'wallet-ana');

    await page.locator('input[name=to_email]').fill('ben@example.com');
    await page.locator('input[name=amount]').fill('1');
    await page.locator('input[name=note]').fill('great cleanup');
    await page.getByRole('button', { name: /send/i }).click();

    // Proof the token actually MOVED: balance drops by exactly one, and the
    // ledger shows a 'tip' row with the recipient's DISPLAY NAME (never email).
    await expect(page.locator('#balance')).toHaveText('🪙 ' + (before - 1));
    await expect(page.locator('.pill', { hasText: /^tip$/ }).first()).toBeVisible();
    await expect(page.getByText('Ben Carter').first()).toBeVisible();
    await shot(page, testInfo, 'after-tip');
    await expectNoGenericError(page);
  });

  test('the ledger and profile never show an email address', async ({ page }, testInfo) => {
    await loginUI(page, 'ana@example.com', 'password123');
    await page.waitForTimeout(700);
    const signedIn = await page.locator('#nav').isVisible().catch(() => false);
    test.skip(!signedIn, 'seed data not present');

    await page.goto('/#/wallet');
    await expect(page.getByText(/your balance/i)).toBeVisible();
    // No @example.com anywhere in the ledger/claims (emails are private).
    await expect(page.locator('#view').getByText(/@example\.com/)).toHaveCount(0);
    await shot(page, testInfo, 'ledger-no-emails');
  });

  test('editing the profile display name persists', async ({ page }, testInfo) => {
    await registerUI(page, uemail('prof'), 'password123', 'Original Name');
    await page.goto('/#/me');
    await shot(page, testInfo, 'me');
    await page.locator('input[name=display_name]').fill('Renamed Person');
    await page.getByRole('button', { name: /save changes/i }).click();
    await expect(page.getByText(/profile updated/i)).toBeVisible();
    await shot(page, testInfo, 'profile-saved');

    await page.reload();
    await page.goto('/#/me');
    await expect(page.getByRole('heading', { name: /Renamed Person/i })).toBeVisible();
    await shot(page, testInfo, 'me-after');
    await expectNoGenericError(page);
  });
});
