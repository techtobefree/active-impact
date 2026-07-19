const { test, expect } = require('@playwright/test');
const { shot, expectNoGenericError, formError, logoutUI, uname } = require('../helpers');

test.describe('Auth', () => {
  test('registration surfaces specific validation messages (never the generic error)', async ({ page }, testInfo) => {
    await page.goto('/#/register');
    await shot(page, testInfo, 'register-blank');

    // Too-short password — must tell the user WHY (this is the bug we regressed on).
    await page.locator('input[name=username]').fill(uname('a'));
    await page.locator('input[name=password]').fill('short');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(formError(page)).toBeVisible();
    await shot(page, testInfo, 'short-password-error');
    await expect(formError(page), 'a rejected password must explain why, not say "something went wrong"')
      .not.toContainText(/something went wrong/i);
    await expect(formError(page)).toContainText(/password/i);

    // Bad username (too short) — specific message too.
    await page.locator('input[name=username]').fill('ab');
    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();
    await shot(page, testInfo, 'bad-username-error');
    await expect(formError(page)).not.toContainText(/something went wrong/i);
    await expect(formError(page)).toContainText(/username/i);

    // An email as the username (a natural instinct — the exact case a real user
    // hit) is rejected with the REASON, never the generic error.
    await page.locator('input[name=username]').fill('someone@example.com');
    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();
    await shot(page, testInfo, 'email-username-error');
    await expect(formError(page)).not.toContainText(/something went wrong/i);
    await expect(formError(page)).toContainText(/username/i);

    // Valid → signed in.
    await page.locator('input[name=username]').fill(uname('ok'));
    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page.locator('#nav')).toBeVisible();
    await shot(page, testInfo, 'registered-home');
    await expectNoGenericError(page);
  });

  test('duplicate username is reported clearly', async ({ page }, testInfo) => {
    const u = uname('dup');
    await page.goto('/#/register');
    await page.locator('input[name=username]').fill(u);
    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page.locator('#nav')).toBeVisible();

    // Sign out (wait for the redirect to complete), then try the same username.
    await logoutUI(page);
    await page.goto('/#/register');
    await page.locator('input[name=username]').fill(u);
    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();
    await shot(page, testInfo, 'duplicate-username');
    await expect(formError(page)).not.toContainText(/something went wrong/i);
    await expect(formError(page)).toContainText(/taken/i);
  });

  test('wrong-password login says so; correct password signs in', async ({ page }, testInfo) => {
    const u = uname('login');
    await page.goto('/#/register');
    await page.locator('input[name=username]').fill(u);
    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page.locator('#nav')).toBeVisible();

    await page.goto('/#/me');
    await page.getByRole('button', { name: /sign out/i }).click();
    await expect(page).toHaveURL(/#\/login/);
    await shot(page, testInfo, 'login-screen');

    await page.locator('input[name=username]').fill(u);
    await page.locator('input[name=password]').fill('nope-wrong-pw');
    await page.getByRole('button', { name: /^sign in$/i }).click();
    await expect(formError(page)).toBeVisible();
    await shot(page, testInfo, 'wrong-password');
    await expect(formError(page)).not.toContainText(/something went wrong/i);
    await expect(formError(page)).toContainText(/wrong username or password/i);

    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /^sign in$/i }).click();
    await expect(page.locator('#nav')).toBeVisible();
    await shot(page, testInfo, 'signed-in');
    await expectNoGenericError(page);
  });
});
