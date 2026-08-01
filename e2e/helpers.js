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

// A unique slug per run (for display names / email local parts).
function uname(tag = '') {
  return ('e2e' + Date.now().toString(36) + (seq++) + tag).toLowerCase().replace(/[^a-z0-9_-]/g, '').slice(0, 30);
}
// A unique, valid email per run.
function uemail(tag = '') {
  return uname(tag) + '@e2e.local';
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

async function registerUI(page, email, password = 'password123', displayName = 'E2E User') {
  await page.goto('/#/register');
  await page.locator('input[name=email]').fill(email);
  await page.locator('input[name=display_name]').fill(displayName); // required — public identity
  await page.locator('input[name=password]').fill(password);
  await page.getByRole('button', { name: /create account/i }).click();
  // A successful convert navigates home; the nav is ALWAYS visible now (guest-first),
  // so it's no longer a "done" signal — wait for the landing instead.
  await expect(page).toHaveURL(/#\/$/);
}

// Sign in on the guest-first CONVERT screen. With an existing email + the right
// password this MERGES the throwaway guest into that account (SERVICE_LOG.md §4);
// the single submit button is "Create account".
async function loginUI(page, email, password = 'password123') {
  await page.goto('/#/login');
  await page.locator('input[name=email]').fill(email);
  await page.locator('input[name=password]').fill(password);
  await page.getByRole('button', { name: /create account/i }).click();
  // Convert (attach a new email OR sign-in-merge an existing one) lands on the feed.
  await expect(page).toHaveURL(/#\/$/);
}

async function logoutUI(page) {
  await page.goto('/#/me');
  await page.getByRole('button', { name: /sign out/i }).click();
  await expect(page).toHaveURL(/#\/login/);
}

// A datetime-local value N days from now ("YYYY-MM-DDTHH:mm", the input's format).
// Always use this rather than a literal: the create form rejects a start time more
// than 12h in the past, so a hardcoded date is a test that passes in the morning
// and fails at night.
function dtLocal(days) {
  const d = new Date(Date.now() + days * 86400e3);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

// Narrow the feed to ONE project by title. The feed pages at 50, and a shared
// test database fills up, so scanning the whole list for a card is a flake
// waiting to happen — search first and the assertion is about that project only.
async function findInFeed(page, title) {
  await page.locator('#view input[type=search]').fill(title);
  await page.waitForTimeout(400); // the search box debounces at 250ms
}

module.exports = {
  shot, expectNoGenericError, formError, fieldError, registerUI, loginUI, logoutUI,
  uname, uemail, slug, findInFeed, dtLocal,
};
