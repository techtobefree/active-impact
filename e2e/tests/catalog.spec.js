const { test, expect } = require('@playwright/test');
const { shot, expectNoGenericError, registerUI, logoutUI, uname, uemail } = require('../helpers');

test.describe('Catalog', () => {
  test('post an offer; a second user finds and claims it', async ({ page }, testInfo) => {
    const title = 'E2E Muffins ' + uname('t');

    // Seller posts a free offer.
    await registerUI(page, uemail('sell'), 'password123', 'Seller');
    await page.getByRole('link', { name: /catalog/i }).first().click();
    await page.getByRole('button', { name: /post/i }).click();
    await page.locator('input[name=title]').fill(title);
    await page.locator('textarea[name=description]').fill('Fresh muffins, pickup downtown.');
    await page.locator('input[name=price_tokens]').fill('0'); // free offer
    await shot(page, testInfo, 'new-offer');
    await page.getByRole('button', { name: /^post$/i }).click();
    await expect(page.getByRole('heading', { name: title })).toBeVisible();
    await shot(page, testInfo, 'offer-detail-seller');
    await expectNoGenericError(page);

    // Buyer signs up and claims it.
    await logoutUI(page);
    await registerUI(page, uemail('buy'), 'password123', 'Buyer');
    await page.goto('/#/catalog');
    await expect(page.getByRole('link', { name: title })).toBeVisible();
    await shot(page, testInfo, 'catalog-list-buyer');
    await page.getByRole('link', { name: title }).click();
    await shot(page, testInfo, 'offer-detail-buyer');
    await page.getByRole('button', { name: /claim/i }).click();
    await expect(page.getByText(/pending/i)).toBeVisible();
    await shot(page, testInfo, 'claim-pending');
    await expectNoGenericError(page);
  });

  test('posting a need shows the tip helper, not a price field', async ({ page }, testInfo) => {
    await registerUI(page, uemail('need'), 'password123', 'Need Poster');
    await page.goto('/#/catalog/new');
    await page.getByRole('button', { name: /need/i }).click();
    await shot(page, testInfo, 'new-need-form');
    // A need has no price field.
    await expect(page.locator('input[name=price_tokens]')).toHaveCount(0);
    await page.locator('input[name=title]').fill('E2E Need a ride ' + uname('n'));
    await page.getByRole('button', { name: /^post$/i }).click();
    await expect(page.getByText(/need/i).first()).toBeVisible();
    await shot(page, testInfo, 'need-detail');
    await expectNoGenericError(page);
  });
});
