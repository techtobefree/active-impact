// Peer check-in — the ATTESTED layer, driven as a user (CHECKIN_PROOF.md).
//
// The claim under test is the whole point of the feature: ONE scan records TWO
// people. Ana checks in with the button (self-reported), Ben opens her personal
// code and confirms, and Ana — who never touched her phone again — becomes
// verified. Both halves are asserted through the UI, not the API.
//
// Headless Chrome has no BarcodeDetector, so the Check in button here also
// exercises the documented FALLBACK: no scanner → plain asserted check-in (§7.1).
const { test, expect } = require('@playwright/test');
const { shot, expectNoGenericError, registerUI, uname, uemail } = require('../helpers');

// A person's code is only ever handed out by its owner, so read it the way the
// owner's own client does: the private /api/me shape.
async function myQrToken(page) {
  return page.evaluate(async () => {
    const r = await fetch('/api/me', {
      headers: { Authorization: 'Bearer ' + localStorage.getItem('ai_token') },
    });
    return (await r.json()).qr_token;
  });
}

test.describe('Peer check-in', () => {
  test('one scan checks in both people: the scanner and the person scanned', async ({ page, browser }, testInfo) => {
    // ---- Ana creates the project and turns up ----
    await registerUI(page, uemail('ana'), 'password123', 'Ana Organizer');
    await page.goto('/#/projects');
    const title = 'E2E Peer Cleanup ' + uname();
    await page.getByRole('link', { name: /new service project/i }).click();
    await page.locator('input[name=title]').fill(title);
    await page.locator('input[name=location_text]').fill('Riverside, north gate');
    await page.locator('input[name=starts_at]').fill('2099-03-01T10:00');
    await page.getByRole('button', { name: /create project/i }).click();
    await expect(page.getByRole('heading', { name: title })).toBeVisible();

    // Reach the event's own screen — that is where a volunteer's code lives.
    await page.getByRole('link', { name: /^manage$/i }).click();
    await expect(page).toHaveURL(/#\/events\/\d+\/lead$/);
    const eventId = page.url().match(/#\/events\/(\d+)\/lead/)[1];
    await page.goto(`/#/events/${eventId}`);

    // Creating a project doesn't RSVP you to its event — the action starts at RSVP.
    await page.getByRole('button', { name: /^rsvp$/i }).click();

    // Check in with the BUTTON. No scanner in headless Chrome → the fallback
    // fires and she is recorded as self-reported (§7.1).
    await page.getByRole('button', { name: /^check in$/i }).click();
    await expect(page.getByText(/self-reported/i)).toBeVisible();
    await expectNoGenericError(page);
    await shot(page, testInfo, 'ana-self-reported');

    // Her personal code for this event — the thing other people scan. Opening it
    // must SURVIVE the check-in above: a whole-view refresh landing late used to
    // rebuild the page and close this card again (only visible on real latency).
    await page.getByText(/show my code/i).click();
    await expect(page.getByRole('img', { name: /personal check-in qr/i })).toBeVisible();
    await expect(page.getByText(/print it and pin it up/i)).toBeVisible();
    await shot(page, testInfo, 'ana-my-code');
    const anaToken = await myQrToken(page);
    expect(anaToken).toBeTruthy();

    // ---- Ben arrives on his own phone and scans Ana's code ----
    // A scanned URL opens a fresh browser: separate context = separate session.
    const benCtx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const ben = await benCtx.newPage();
    await ben.goto('/#/'); // boot mints Ben a guest session first, as on a real phone
    await ben.goto(`/#/s/${anaToken}/${eventId}`);

    await expect(ben.getByText('Checking in with')).toBeVisible();
    // Named twice on purpose: the banner and the confirm copy.
    await expect(ben.getByText('Ana Organizer').first()).toBeVisible();
    await expect(ben.getByText('Volunteer waiver')).toBeVisible();
    await shot(ben, testInfo, 'ben-scanned-anas-code');

    await ben.getByRole('button', { name: /confirm/i }).click();
    await expect(ben.getByText(/verified/i).first()).toBeVisible();
    await expectNoGenericError(ben);
    await shot(ben, testInfo, 'ben-verified');

    // ---- The payoff: Ana is now verified too, without touching her phone ----
    await page.reload();
    await expect(page.getByText(/✅ Verified/i).first()).toBeVisible();
    await expect(page.getByText(/self-reported/i)).toHaveCount(0);
    await shot(page, testInfo, 'ana-now-verified');

    // The organizer's roster tells the two apart at a glance.
    await page.goto(`/#/events/${eventId}/lead`);
    await expect(page.getByText(/✅ Verified/i).first()).toBeVisible();
    await shot(page, testInfo, 'roster-verified');

    await benCtx.close();
  });

  test('scanning your own code is refused, kindly', async ({ page }, testInfo) => {
    await registerUI(page, uemail('solo'), 'password123', 'Solo Volunteer');
    await page.goto('/#/projects');
    const title = 'E2E Solo Cleanup ' + uname();
    await page.getByRole('link', { name: /new service project/i }).click();
    await page.locator('input[name=title]').fill(title);
    await page.locator('input[name=location_text]').fill('Somewhere');
    await page.locator('input[name=starts_at]').fill('2099-03-01T10:00');
    await page.getByRole('button', { name: /create project/i }).click();
    await page.getByRole('link', { name: /^manage$/i }).click();
    const eventId = page.url().match(/#\/events\/(\d+)\/lead/)[1];

    await page.goto(`/#/events/${eventId}`);
    await page.getByRole('button', { name: /^rsvp$/i }).click();
    await page.getByRole('button', { name: /^check in$/i }).click();
    await expect(page.getByText(/self-reported/i)).toBeVisible();
    const token = await myQrToken(page);

    // A code only counts when somebody ELSE reads it — say so, don't dead-end.
    await page.goto(`/#/s/${token}/${eventId}`);
    await expect(page.getByText(/your own/i).first()).toBeVisible();
    await expect(page.getByRole('button', { name: /confirm/i })).toHaveCount(0);
    await expectNoGenericError(page);
    await shot(page, testInfo, 'own-code');
  });

  test('an unknown personal code fails friendly, not blank', async ({ page }, testInfo) => {
    await registerUI(page, uemail('lost'), 'password123', 'Lost Scanner');
    await page.goto('/#/s/definitely-not-a-real-token/999999');
    await expect(page.getByText(/didn't work/i)).toBeVisible();
    await expect(page.getByRole('link', { name: /back to projects/i })).toBeVisible();
    await shot(page, testInfo, 'invalid-code');
  });
});
