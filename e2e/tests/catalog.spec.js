const { test, expect } = require('@playwright/test');
const { shot, expectNoGenericError, registerUI, loginUI, logoutUI, uname, uemail } = require('../helpers');

// Post an offer, then claim it. Claiming settles on the spot (T11) and the
// price is destroyed rather than paid to the poster (T12).

async function postOffer(page, title, price) {
  await page.goto('/#/catalog/new');
  await page.locator('input[name=title]').fill(title);
  await page.locator('textarea[name=description]').fill('Fresh muffins, pickup downtown.');
  await page.locator('input[name=price_tokens]').fill(String(price));
  await page.getByRole('button', { name: /^post$/i }).click();
  await expect(page.getByRole('heading', { name: title })).toBeVisible();
}

function balanceOf(page) {
  return page.locator('#balance').textContent()
    .then((t) => parseInt((t || '').replace(/\D/g, ''), 10) || 0);
}

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
    // The whole card is now the link — clicking it (not a title anchor) navigates.
    const card = page.locator('a.card', { hasText: title });
    await expect(card).toBeVisible();
    await shot(page, testInfo, 'catalog-list-buyer');
    await card.click();
    await shot(page, testInfo, 'offer-detail-buyer');
    // No waiting on anybody: the claim is settled the moment it is made.
    await page.getByRole('button', { name: /claim/i }).click();
    await expect(page.getByText(/redeemed/i).first()).toBeVisible();
    await expect(page.getByText(/show this screen as proof/i)).toBeVisible();
    await shot(page, testInfo, 'claim-redeemed');
    await expectNoGenericError(page);
  });

  // Self-sufficient (no seed data), so this one runs against production too: the
  // settlement is a single transaction, and a claimant who cannot cover the
  // price must leave nothing behind — no claim, no decrement, no tokens.
  test('a claim you cannot afford is refused, and nothing is left behind', async ({ page }, testInfo) => {
    const title = 'E2E Too Dear ' + uname('t');
    await registerUI(page, uemail('richsell'), 'password123', 'Pricey Seller');
    await postOffer(page, title, 9);
    const itemUrl = page.url();

    await logoutUI(page);
    await registerUI(page, uemail('brokebuy'), 'password123', 'Broke Buyer');
    await page.goto(itemUrl);
    page.once('dialog', (d) => d.accept());
    await page.getByRole('button', { name: /claim for 9/i }).click();

    // The specific message, not a generic failure.
    await expect(page.getByText(/don't have enough tokens/i)).toBeVisible();
    await expect(page.getByText(/show this screen as proof/i)).toHaveCount(0);
    await shot(page, testInfo, 'cannot-afford');
    await expectNoGenericError(page);

    // Reload: the refusal was real, not just a toast. Still claimable, and the
    // quantity/stock story is unchanged.
    await page.reload();
    await expect(page.getByRole('button', { name: /claim for 9/i })).toBeVisible();
    await expect(page.getByText(/redeemed/i)).toHaveCount(0);
    await expectNoGenericError(page);
  });

  test('claiming a priced offer burns the tokens — the poster is not paid', async ({ page }, testInfo) => {
    const title = 'E2E Bike Tune ' + uname('t');

    // A poster with an empty wallet, so "still zero" at the end means something.
    const posterEmail = uemail('burnsell');
    await registerUI(page, posterEmail, 'password123', 'Burn Seller');
    await page.goto('/#/wallet');
    await expect(page.getByText(/your balance/i)).toBeVisible();
    expect(await balanceOf(page)).toBe(0);
    await postOffer(page, title, 1);
    const itemUrl = page.url();

    // A funded claimant (seeded by scripts/seed.py). Skips cleanly if absent.
    await logoutUI(page);
    await loginUI(page, 'ana@example.com');
    await page.waitForTimeout(700);
    const signedIn = await page.locator('#nav').isVisible().catch(() => false);
    test.skip(!signedIn, 'seed data (ana@example.com) not present — run: python scripts/seed.py');
    await page.goto('/#/wallet');
    await expect(page.getByText(/your balance/i)).toBeVisible();
    const before = await balanceOf(page);
    test.skip(before < 1, 'seeded user ana has no tokens left — re-run: python scripts/seed.py');

    // The burn is irreversible, so the app says so before doing it.
    await page.goto(itemUrl);
    page.once('dialog', (d) => {
      expect(d.message()).toMatch(/retired straight away|cannot be undone/i);
      d.accept();
    });
    await page.getByRole('button', { name: /claim for 1/i }).click();
    await expect(page.getByText(/show this screen as proof/i)).toBeVisible();
    await shot(page, testInfo, 'priced-claim-redeemed');

    // The claimant paid, and the ledger names no recipient — because there isn't one.
    await expect(page.locator('#balance')).toHaveText('🪙 ' + (before - 1));
    await page.goto('/#/wallet');
    await expect(page.getByText(/retired — out of circulation/i).first()).toBeVisible();
    await shot(page, testInfo, 'ledger-burn-row');
    await expectNoGenericError(page);

    // The poster is credited with the deed, not the tokens.
    await logoutUI(page);
    await loginUI(page, posterEmail);
    await page.goto('/#/wallet');
    await expect(page.getByText(/your balance/i)).toBeVisible();
    expect(await balanceOf(page)).toBe(0);
    await page.goto(itemUrl);
    await expect(page.getByText(/1 🪙 retired/i).first()).toBeVisible();
    await expect(page.getByRole('button', { name: /^(accept|decline)$/i })).toHaveCount(0);
    await shot(page, testInfo, 'poster-sees-the-record-not-the-tokens');
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
