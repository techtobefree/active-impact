const { test, expect, devices } = require('@playwright/test');
const { shot, expectNoGenericError, registerUI, uemail } = require('../helpers');

// Web Push from the person's side (PUSH.md §6). The point of these tests is that
// somebody always learns WHY they will or will not be buzzed — and that nobody is
// ever shown a switch that cannot work.
//
// The push SERVICE is stubbed (headless Chrome has no FCM connection, and Apple's
// push service is not something a test suite should be talking to). Everything of
// ours — asking permission, fetching the key, registering the device, and what the
// screen says in each state — is real.

async function stubPushManager(page, { endpoint = 'https://push.example/e2e' } = {}) {
  await page.addInitScript((ep) => {
    const fake = {
      endpoint: ep,
      toJSON: () => ({ endpoint: ep, keys: { p256dh: 'e2e-p256dh', auth: 'e2e-auth' } }),
      unsubscribe: () => Promise.resolve(true),
    };
    let current = null;
    // Replace only the platform call that needs a real push service.
    Object.defineProperty(window, 'PushManager', { value: function PushManager() {}, writable: true });
    const install = (reg) => {
      // defineProperty, NOT assignment: `pushManager` is a read-only accessor on
      // the prototype, so `reg.pushManager = …` is a silent no-op in sloppy mode
      // and the REAL push manager would answer instead.
      Object.defineProperty(reg, 'pushManager', {
        configurable: true,
        value: {
          subscribe: () => { current = fake; return Promise.resolve(fake); },
          getSubscription: () => Promise.resolve(current),
        },
      });
      return reg;
    };
    const ready = navigator.serviceWorker.ready;
    Object.defineProperty(navigator.serviceWorker, 'ready', {
      get: () => ready.then(install),
      configurable: true,
    });
  }, endpoint);
}

test.describe('Notifications on the phone', () => {
  test('a supported browser is offered the switch, and turning it on registers the device',
    async ({ page, context }, testInfo) => {
      await context.grantPermissions(['notifications']);
      await stubPushManager(page);
      await registerUI(page, uemail('push'), 'password123', 'Push Tester');
      await page.goto('/#/notifications');

      const card = page.locator('.card', { hasText: 'On this phone' });
      await expect(card.getByRole('button', { name: /notify me on this phone/i })).toBeVisible();
      await shot(page, testInfo, 'offered');

      await card.getByRole('button', { name: /notify me on this phone/i }).click();
      await expect(card.getByText(/this device will buzz/i)).toBeVisible();
      await expect(card.getByRole('button', { name: /turn off on this device/i })).toBeVisible();
      await shot(page, testInfo, 'on');
      await expectNoGenericError(page);

      // The server really has the device — the screen is not just claiming it.
      const status = await page.evaluate(async () => {
        const t = localStorage.getItem('ai_token');
        const r = await fetch('/api/push/status?endpoint=' + encodeURIComponent('https://push.example/e2e'),
          { headers: { Authorization: 'Bearer ' + t } });
        return r.json();
      });
      expect(status).toEqual({ subscribed: true });

      // …and turning it off unregisters it.
      await card.getByRole('button', { name: /turn off on this device/i }).click();
      await expect(card.getByRole('button', { name: /notify me on this phone/i })).toBeVisible();
      const after = await page.evaluate(async () => {
        const t = localStorage.getItem('ai_token');
        const r = await fetch('/api/push/status?endpoint=' + encodeURIComponent('https://push.example/e2e'),
          { headers: { Authorization: 'Bearer ' + t } });
        return r.json();
      });
      expect(after).toEqual({ subscribed: false });
    });

  test('an iPhone that has not been installed gets the Home Screen steps, not a dead switch',
    async ({ browser }, testInfo) => {
      // A real iPhone Safari tab: no PushManager at all, and not standalone.
      const ctx = await browser.newContext({
        ...devices['iPhone 13'],
        userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 '
          + '(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
      });
      const page = await ctx.newPage();
      await page.addInitScript(() => {
        delete window.PushManager;            // exactly what iOS Safari does in a tab
      });
      await registerUI(page, uemail('ios'), 'password123', 'iPhone User');
      await page.goto('/#/notifications');

      const card = page.locator('.card', { hasText: 'On this phone' });
      await expect(card.getByText(/add active impact to your home screen/i)).toBeVisible();
      await expect(card.getByText(/add to home screen/i).first()).toBeVisible();
      await expect(card.getByText(/share/i).first()).toBeVisible();
      // The important half: no switch that could never work.
      await expect(card.getByRole('button')).toHaveCount(0);
      await shot(page, testInfo, 'ios-install-nudge');
      await expectNoGenericError(page);
      await ctx.close();
    });

  test('a browser that cannot do it says so plainly', async ({ browser }, testInfo) => {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await ctx.newPage();
    await page.addInitScript(() => { delete window.PushManager; });   // desktop, unsupported
    await registerUI(page, uemail('nopush'), 'password123', 'No Push');
    await page.goto('/#/notifications');

    const card = page.locator('.card', { hasText: 'On this phone' });
    await expect(card.getByText(/can't send notifications to your phone/i)).toBeVisible();
    await expect(card.getByText(/bell above still works/i)).toBeVisible();
    await expect(card.getByRole('button')).toHaveCount(0);
    await shot(page, testInfo, 'unsupported');
    await ctx.close();
  });

  test('a blocked permission is explained, not silently retried', async ({ browser }, testInfo) => {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await ctx.newPage();
    await page.addInitScript(() => {
      Object.defineProperty(Notification, 'permission', { get: () => 'denied' });
    });
    await registerUI(page, uemail('denied'), 'password123', 'Denied');
    await page.goto('/#/notifications');

    const card = page.locator('.card', { hasText: 'On this phone' });
    await expect(card.getByText(/blocked for this site/i)).toBeVisible();
    await expect(card.getByText(/won't let us ask again/i)).toBeVisible();
    await expect(card.getByRole('button')).toHaveCount(0);
    await shot(page, testInfo, 'denied');
    await ctx.close();
  });
});
