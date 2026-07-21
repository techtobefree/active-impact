const { test, expect } = require('@playwright/test');
const { shot, expectNoGenericError, uname, uemail } = require('../helpers');

// A valid 1×1 PNG (resizeImage draws it onto a canvas, so it must decode).
const PNG_1x1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
  'base64',
);

// Log a photo + caption from the current (already booted) log screen.
async function postRecord(page, caption) {
  await page.locator('input[type=file]').setInputFiles({ name: 'act.png', mimeType: 'image/png', buffer: PNG_1x1 });
  await page.locator('#view textarea').fill(caption);
  await page.getByRole('button', { name: /^post$/i }).click();
  await expect(page).toHaveURL(/#\/$/);
  await expect(page.locator('article.record').first()).toContainText(caption); // newest → top
}

test.describe('Service log', () => {
  test('first run lands on Log; a photo+caption tops the feed; cheer toggles the count', async ({ page }, testInfo) => {
    // Brand-new visitor → a guest is minted and dropped straight onto the Log screen.
    await page.goto('/');
    await expect(page).toHaveURL(/#\/log$/);
    await expect(page.locator('#view textarea')).toBeVisible();
    await shot(page, testInfo, 'first-run-log');

    const caption = 'Picked up litter ' + uname('cap');
    await postRecord(page, caption);
    await shot(page, testInfo, 'record-in-feed');
    await expectNoGenericError(page);

    // Cheer flips the count up, then back down (optimistic + server-reconciled).
    const cheer = page.locator('article.record').first().getByRole('button', { name: /cheer/i });
    await cheer.click();
    await expect(cheer).toContainText('1');
    await shot(page, testInfo, 'cheered');
    await cheer.click();
    await expect(cheer).toContainText('0');
    await expectNoGenericError(page);
  });

  test('a second fresh guest sees the record and can cheer it', async ({ page }, testInfo) => {
    // First guest logs a record.
    await page.goto('/');
    await expect(page).toHaveURL(/#\/log$/);
    const caption = 'Planted trees ' + uname('two');
    await postRecord(page, caption);

    // Become a brand-new guest: clear the session and re-boot.
    await page.evaluate(() => localStorage.clear());
    await page.reload();
    await expect(page).toHaveURL(/#\/log$/); // fresh guest → first-run Log (also proves boot finished)
    await page.goto('/#/');

    // The public feed shows the first guest's record; this guest can cheer it.
    const card = page.locator('article.record', { hasText: caption }).first();
    await expect(card).toBeVisible();
    const cheer = card.getByRole('button', { name: /cheer/i });
    await cheer.click();
    await expect(cheer).toContainText('1');
    await shot(page, testInfo, 'second-guest-cheered');
    await expectNoGenericError(page);
  });

  test('convert a guest to a real account; the record stays mine; then delete it', async ({ page }, testInfo) => {
    await page.goto('/');
    await expect(page).toHaveURL(/#\/log$/);
    const caption = 'Cooked meals ' + uname('mine');
    await postRecord(page, caption);

    // Convert from Me (guest screen shows the "create an account" convert card).
    await page.goto('/#/me');
    await page.locator('input[name=email]').fill(uemail('conv'));
    await page.locator('input[name=password]').fill('password123');
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page.getByText(/account saved/i)).toBeVisible(); // convert succeeded
    await shot(page, testInfo, 'converted');
    await expectNoGenericError(page);

    // Back on the feed the record is STILL mine → its ⋯ menu offers Delete.
    await page.goto('/#/');
    const card = page.locator('article.record', { hasText: caption }).first();
    await expect(card).toBeVisible();
    await card.getByRole('button', { name: /more/i }).click();
    page.once('dialog', (d) => d.accept()); // the delete confirm
    await card.getByRole('button', { name: /delete/i }).click();
    await expect(page.locator('article.record', { hasText: caption })).toHaveCount(0);
    await shot(page, testInfo, 'deleted');
    await expectNoGenericError(page);
  });
});
