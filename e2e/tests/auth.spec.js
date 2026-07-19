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

    // Short password -> under the PASSWORD field, with the reason.
    await page.locator('input[name=email]').fill(uemail('a'));
    await page.locator('input[name=display_name]').fill('Test Person');
    await page.locator('input[name=password]').fill('short');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(fieldError(page, 'password')).toContainText(/8 characters/);
    await shot(page, testInfo, 'short-password-under-field');
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
    await expect(page.locator('#nav')).toBeVisible();
    await shot(page, testInfo, 'registered');
    await expectNoGenericError(page);
  });

  test('duplicate email is flagged under the email field', async ({ page }, testInfo) => {
    const em = uemail('dup');
    await page.goto('/#/register');
    await page.locator('input[name=email]').fill(em);
    await page.locator('input[name=display_name]').fill('First Person');
    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page.locator('#nav')).toBeVisible();

    await logoutUI(page);
    await page.goto('/#/register');
    await page.locator('input[name=email]').fill(em);
    await page.locator('input[name=display_name]').fill('Second Person');
    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(fieldError(page, 'email')).toContainText(/already exists|sign in/i);
    await shot(page, testInfo, 'duplicate-under-field');
  });

  test('wrong-password login says so; correct password signs in', async ({ page }, testInfo) => {
    const em = uemail('login');
    await page.goto('/#/register');
    await page.locator('input[name=email]').fill(em);
    await page.locator('input[name=display_name]').fill('Login Tester');
    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page.locator('#nav')).toBeVisible();

    await logoutUI(page);
    await page.locator('input[name=email]').fill(em);
    await page.locator('input[name=password]').fill('nope-wrong-pw');
    await page.getByRole('button', { name: /^sign in$/i }).click();
    await expect(formError(page)).toContainText(/wrong email or password/i);
    await shot(page, testInfo, 'wrong-password');

    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /^sign in$/i }).click();
    await expect(page.locator('#nav')).toBeVisible();
    await shot(page, testInfo, 'signed-in');
    await expectNoGenericError(page);
  });
});
