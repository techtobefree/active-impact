const { test, expect } = require('@playwright/test');
const { shot, expectNoGenericError, formError, fieldError, logoutUI, uname, uemail } = require('../helpers');

test.describe('Auth (email + password)', () => {
  test('validation errors appear under the exact field that caused them', async ({ page }, testInfo) => {
    await page.goto('/#/register');
    await shot(page, testInfo, 'register-blank');

    // Not an email — flagged live, under the EMAIL field, before any server call.
    await page.locator('input[name=email]').fill('not-an-email');
    await page.locator('input[name=password]').click(); // just moving focus flags it
    await expect(fieldError(page, 'email')).toBeVisible();
    await shot(page, testInfo, 'bad-email-flagged-live');
    await expect(fieldError(page, 'email')).not.toContainText(/something went wrong/i);
    await expect(fieldError(page, 'password')).toHaveCount(0); // no cross-field noise

    // 1-char minimum means no "too short" case, but a password with an edge space
    // is still flagged live under the PASSWORD field.
    await page.locator('input[name=email]').fill(uemail('a'));
    await page.locator('input[name=display_name]').fill('Test Person');
    await page.locator('input[name=password]').fill(' spacey');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(fieldError(page, 'password')).toContainText(/space/i);
    await shot(page, testInfo, 'space-password-under-field');
    await expect(fieldError(page, 'email')).toHaveCount(0);

    await expectNoGenericError(page);
  });

  test('email is auto-lowercased; registration with a real-looking email succeeds', async ({ page }, testInfo) => {
    await page.goto('/#/register');
    const local = uname('case');
    await page.locator('input[name=email]').pressSequentially(local.toUpperCase() + '@E2E.LOCAL');
    await expect(page.locator('input[name=email]')).toHaveValue(local + '@e2e.local');
    await page.locator('input[name=display_name]').fill('Case Tester');
    await page.locator('input[name=password]').fill('admin1234');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page).toHaveURL(/#\/$/); // convert landed home = it actually completed
    await shot(page, testInfo, 'registered');
    await expectNoGenericError(page);
  });

  // Convert semantics (SERVICE_LOG.md §4/D7): the guest-first screen no longer
  // "rejects a duplicate email" — an existing email + the RIGHT password signs you
  // into that account and merges the throwaway guest's logs into it.
  test('an existing email + right password signs into that account (merge)', async ({ page }, testInfo) => {
    const em = uemail('dup');
    await page.goto('/#/register');
    await page.locator('input[name=email]').fill(em);
    await page.locator('input[name=display_name]').fill('First Person');
    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page).toHaveURL(/#\/$/); // convert landed home = it actually completed

    // Sign out (drops to a fresh guest) → convert to the SAME email with the right
    // password → merged back into the original account.
    await logoutUI(page);
    await page.goto('/#/register');
    await page.locator('input[name=email]').fill(em);
    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page).toHaveURL(/#\/$/); // convert landed home = it actually completed

    // It is the ORIGINAL account (First Person), not a freshly created one.
    await page.goto('/#/me');
    await expect(page.getByRole('heading', { name: /First Person/i })).toBeVisible();
    await shot(page, testInfo, 'merged-into-original');
    await expectNoGenericError(page);
  });

  test('an existing email + wrong password is rejected; the right one then signs in', async ({ page }, testInfo) => {
    const em = uemail('login');
    await page.goto('/#/register');
    await page.locator('input[name=email]').fill(em);
    await page.locator('input[name=display_name]').fill('Login Tester');
    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page).toHaveURL(/#\/$/); // convert landed home = it actually completed

    await logoutUI(page);
    await page.locator('input[name=email]').fill(em);
    await page.locator('input[name=password]').fill('nope-wrong-pw');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(formError(page)).toContainText(/wrong email or password/i);
    await shot(page, testInfo, 'wrong-password');

    // The guest session survives the wrong password, so the right one just works.
    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page).toHaveURL(/#\/$/); // convert landed home = it actually completed
    await shot(page, testInfo, 'signed-in');
    await expectNoGenericError(page);
  });
});
