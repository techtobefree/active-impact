const { test, expect } = require('@playwright/test');
const { registerUI, uemail, shot } = require('../helpers');

// Regression guard for the chrome toggle bug: updateChrome used
// classList.toggle('hidden', isPublic) where isPublic is `undefined` on protected
// routes, so the force arg was ignored and the top bar + bottom nav FLIPPED on
// every in-app navigation. They must stay visible on every signed-in page.
test.describe('Chrome (top bar + bottom nav)', () => {
  test('top bar and bottom nav stay visible across in-app navigation', async ({ page }, testInfo) => {
    await registerUI(page, uemail('chrome'), 'password123', 'Chrome Tester');
    const nav = page.locator('#nav');
    const topbar = page.locator('#topbar');
    await expect(nav).toBeVisible();
    await expect(topbar).toBeVisible();

    // Click every tab in turn (hashchange, no reload) — asserting after EACH hop,
    // because the bug flipped the chrome on alternate navigations. Selected by
    // data-tab: the 4-item bar (Home/Catalog/Wallet/Me) has overlapping words
    // ("Home" contains "me"), so a name regex would be ambiguous.
    const hops = [
      ['catalog', /#\/catalog$/],
      ['wallet', /#\/wallet$/],
      ['me', /#\/me$/],
      ['home', /#\/$/],
      ['catalog', /#\/catalog$/],
    ];
    for (const [tab, urlRe] of hops) {
      await page.locator(`#nav a[data-tab="${tab}"]`).click();
      await expect(page).toHaveURL(urlRe);
      await expect(nav, `nav hidden after navigating to ${urlRe}`).toBeVisible();
      await expect(topbar, `top bar hidden after navigating to ${urlRe}`).toBeVisible();
    }
    await shot(page, testInfo, 'chrome-after-tab-cycle');
  });
});
