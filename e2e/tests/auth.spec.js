const { test, expect } = require('@playwright/test');
const { shot, expectNoGenericError, formError, fieldError, logoutUI, uname } = require('../helpers');

test.describe('Auth', () => {
  test('validation errors appear under the exact field that caused them', async ({ page }, testInfo) => {
    await page.goto('/#/register');
    await shot(page, testInfo, 'register-blank');

    // Email in the username (the real incident: autofill leaves it there) —
    // flagged live, under the USERNAME field, before any server call.
    await page.locator('input[name=username]').fill('someone@example.com');
    await page.locator('input[name=password]').click(); // just moving focus flags it
    await expect(fieldError(page, 'username')).toContainText(/handle/i);
    await shot(page, testInfo, 'email-username-flagged-live');
    await expect(fieldError(page, 'password')).toHaveCount(0); // no cross-field noise

    // Short password -> under the PASSWORD field, with the reason.
    await page.locator('input[name=username]').fill(uname('a'));
    await page.locator('input[name=password]').fill('short');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(fieldError(page, 'password')).toContainText(/8 characters/);
    await shot(page, testInfo, 'short-password-under-field');
    await expect(fieldError(page, 'username')).toHaveCount(0);

    // Too-short username -> under the USERNAME field.
    await page.locator('input[name=username]').fill('ab');
    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(fieldError(page, 'username')).toContainText(/3 characters/);
    await shot(page, testInfo, 'short-username-under-field');

    // Nothing anywhere may ever be the generic message.
    await expectNoGenericError(page);
  });

  test('typing a capitalized username is auto-lowercased; registration succeeds', async ({ page }, testInfo) => {
    await page.goto('/#/register');
    const u = uname('case');
    await page.locator('input[name=username]').pressSequentially('X' + u.slice(1).toUpperCase());
    await expect(page.locator('input[name=username]')).toHaveValue('x' + u.slice(1).toLowerCase());
    await page.locator('input[name=password]').fill('admin1234'); // a real password a user tried
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page.locator('#nav')).toBeVisible();
    await shot(page, testInfo, 'registered');
    await expectNoGenericError(page);
  });

  test('duplicate username is flagged under the username field', async ({ page }, testInfo) => {
    const u = uname('dup');
    await page.goto('/#/register');
    await page.locator('input[name=username]').fill(u);
    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page.locator('#nav')).toBeVisible();

    await logoutUI(page);
    await page.goto('/#/register');
    await page.locator('input[name=username]').fill(u);
    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(fieldError(page, 'username')).toContainText(/taken/i);
    await shot(page, testInfo, 'duplicate-under-field');
  });

  test('wrong-password login says so; correct password signs in', async ({ page }, testInfo) => {
    const u = uname('login');
    await page.goto('/#/register');
    await page.locator('input[name=username]').fill(u);
    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page.locator('#nav')).toBeVisible();

    await logoutUI(page);
    await page.locator('input[name=username]').fill(u);
    await page.locator('input[name=password]').fill('nope-wrong-pw');
    await page.getByRole('button', { name: /^sign in$/i }).click();
    await expect(formError(page)).toContainText(/wrong username or password/i);
    await shot(page, testInfo, 'wrong-password');

    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /^sign in$/i }).click();
    await expect(page.locator('#nav')).toBeVisible();
    await shot(page, testInfo, 'signed-in');
    await expectNoGenericError(page);
  });
});
