// Shared helpers: per-step screenshots + expectation guards + UI flows.
const { expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

const SHOTS = path.join(__dirname, 'screenshots');
const counters = new Map();
let seq = 0;

function slug(s) {
  return String(s).replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '').toLowerCase();
}

// A unique, valid (^[a-z0-9_-]{3,30}$) username per run.
function uname(tag = '') {
  return ('e2e' + Date.now().toString(36) + (seq++) + tag).toLowerCase().replace(/[^a-z0-9_-]/g, '').slice(0, 30);
}

// Screenshot a step -> screenshots/<test>/NN-label.png, and attach to the HTML report.
async function shot(page, testInfo, label) {
  const key = testInfo.titlePath.join(' > ');
  const n = (counters.get(key) || 0) + 1;
  counters.set(key, n);
  const dir = path.join(SHOTS, slug(testInfo.title));
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `${String(n).padStart(2, '0')}-${slug(label)}.png`);
  await page.screenshot({ path: file });
  await testInfo.attach(label, { path: file, contentType: 'image/png' });
  return file;
}

// The lesson from the register bug: a swallowed error must never reach the user
// as the generic message. Call after any behavior that should have succeeded.
async function expectNoGenericError(page) {
  await expect(
    page.getByText('Something went wrong', { exact: false }),
    'the generic error is showing — a real error/detail was swallowed by the UI',
  ).toHaveCount(0);
}

// The first visible form error — field-attributed (.field-msg) or general (.field-error).
function formError(page) {
  return page.locator('form .field-error:visible, form .field-msg:visible').first();
}

// The error shown under one SPECIFIC field (the attribution the UI must get right).
function fieldError(page, name) {
  return page.locator(`input[name=${name}] ~ .field-msg:visible, textarea[name=${name}] ~ .field-msg:visible`);
}

async function registerUI(page, username, password = 'password123', displayName) {
  await page.goto('/#/register');
  await page.locator('input[name=username]').fill(username);
  if (displayName) await page.locator('input[name=display_name]').fill(displayName);
  await page.locator('input[name=password]').fill(password);
  await page.getByRole('button', { name: /create account/i }).click();
  await expect(page.locator('#nav')).toBeVisible(); // signed in -> chrome appears
}

async function loginUI(page, username, password = 'password123') {
  await page.goto('/#/login');
  await page.locator('input[name=username]').fill(username);
  await page.locator('input[name=password]').fill(password);
  await page.getByRole('button', { name: /^sign in$/i }).click();
}

async function logoutUI(page) {
  await page.goto('/#/me');
  await page.getByRole('button', { name: /sign out/i }).click();
  await expect(page).toHaveURL(/#\/login/);
}

module.exports = {
  shot, expectNoGenericError, formError, fieldError, registerUI, loginUI, logoutUI, uname, slug,
};
