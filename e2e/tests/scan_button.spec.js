const { test, expect } = require('@playwright/test');
const { shot, expectNoGenericError, registerUI, uname, uemail } = require('../helpers');

// The app bar's scanner (CHECKIN_PROOF.md §7.1b): a code carries its own event,
// so scanning IS checking in — from anywhere, without finding the project first.
//
// Headless Chrome ships no BarcodeDetector, which makes both halves testable: as
// shipped it exercises the "no scanner here" explanation, and a stub exercises
// the routing. The stub is only the platform API — every line of ours is real.
async function stubScanner(page, rawValue) {
  await page.addInitScript((value) => {
    class FakeBarcodeDetector {
      static getSupportedFormats() { return Promise.resolve(['qr_code']); }
      detect() { return Promise.resolve(value ? [{ rawValue: value }] : []); }
    }
    window.BarcodeDetector = FakeBarcodeDetector;
    // A canvas capture stream is a real MediaStream, so <video> plays it happily.
    navigator.mediaDevices = navigator.mediaDevices || {};
    navigator.mediaDevices.getUserMedia = () => {
      const c = document.createElement('canvas');
      c.width = 320; c.height = 240;
      c.getContext('2d').fillRect(0, 0, 320, 240);
      return Promise.resolve(c.captureStream(10));
    };
  }, rawValue);
  // Init scripts apply from the NEXT load, and goto() to an identical URL does
  // not reload — so reload explicitly (the session survives in localStorage).
  await page.reload();
}

// A live event + its check-in code, straight from the API (the leader's view).
async function liveEventCode(page, title) {
  return page.evaluate(async (t) => {
    const token = localStorage.getItem('ai_token');
    const h = { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token };
    const p = await (await fetch('/api/projects', {
      method: 'POST',
      headers: h,
      body: JSON.stringify({
        title: t,
        location_text: 'Riverside Park, boathouse',
        starts_at: new Date(Date.now() - 20 * 60 * 1000).toISOString(),
        expected_minutes: 180,
      }),
    })).json();
    const ev = await (await fetch(`/api/events/${p.events[0].id}`, { headers: h })).json();
    return { eventId: ev.id, code: ev.checkin_code };   // leader-only field
  }, title);
}

test.describe('Scan from the app bar', () => {
  test('the button is on every screen and never claims a tab', async ({ page }, testInfo) => {
    await registerUI(page, uemail('scanbar'), 'password123', 'Scan Barer');
    for (const hash of ['#/', '#/catalog', '#/wallet', '#/me']) {
      await page.goto('/' + hash);
      await expect(page.locator('#scan-btn')).toBeVisible();
    }
    await page.goto('/#/scan');
    // It is not a tab: nothing in the bottom bar lights up for it.
    await expect(page.locator('#nav a.active')).toHaveCount(0);
    await shot(page, testInfo, 'scan-screen');
  });

  test('scanning an event code checks you in without visiting the project', async ({ page }, testInfo) => {
    await registerUI(page, uemail('scanci'), 'password123', 'Scanning Volunteer');
    const title = 'E2E Scan Cleanup ' + uname();
    const { code } = await liveEventCode(page, title);

    // Now become someone holding a camera pointed at that event's sign.
    await stubScanner(page, `https://sidekick.center/#/c/${code}`);
    await page.locator('#scan-btn').click();

    // Straight to the check-in landing — the waiver, then one tap.
    await expect(page).toHaveURL(new RegExp(`#/c/${code}$`));
    await expect(page.getByText('Volunteer waiver')).toBeVisible();
    await shot(page, testInfo, 'scanned-to-waiver');
    await page.getByRole('button', { name: /i agree/i }).click();
    await expect(page.getByText(/checked in/i)).toBeVisible();
    await shot(page, testInfo, 'checked-in-from-scan');
    await expectNoGenericError(page);
  });

  test('a foreign code is refused with a way to try again', async ({ page }, testInfo) => {
    await registerUI(page, uemail('scanbad'), 'password123', 'Bad Code Scanner');
    await stubScanner(page, 'https://example.com/not-us');
    await page.locator('#scan-btn').click();
    await expect(page.getByText(/isn't an Active Impact code/i)).toBeVisible();
    await expect(page.getByRole('button', { name: /scan again/i })).toBeVisible();
    await shot(page, testInfo, 'foreign-code');
    await expectNoGenericError(page);
  });

  test('no in-app scanner: explain the camera app, do not dead-end', async ({ page }, testInfo) => {
    // No stub — exactly what an iPhone sees today.
    await registerUI(page, uemail('scannone'), 'password123', 'Safari Shaped');
    await page.goto('/#/wallet');
    await page.locator('#scan-btn').click();
    await expect(page.getByRole('heading', { name: /use your camera app/i })).toBeVisible();
    await expect(page.getByText(/your phone's own camera works/i)).toBeVisible();
    await shot(page, testInfo, 'no-scanner');
    await expectNoGenericError(page);
  });
});
