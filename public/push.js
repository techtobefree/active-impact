// Web Push from the browser's side (PUSH.md): what state this device is in, and
// how to turn it on or off. Platform APIs only — no library, no build step (D4).
//
// The state matters as much as the switch. On an iPhone that has NOT been added
// to the Home Screen, `PushManager` does not exist at all — so a settings toggle
// there would silently do nothing forever. We detect that case by name and the
// screen says "Add to Home Screen" instead (§6).
import { api } from './api.js';
import { isStandalone } from './ui.js';

export function pushSupported() {
  return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
}

const isIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent)
  // iPadOS 13+ reports as a Mac; the touch points give it away.
  || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

// The service worker is what receives a push, so nothing works until it is ready.
// Bounded: a registration that never settles must not hang the screen.
async function registration(ms = 4000) {
  return Promise.race([
    navigator.serviceWorker.ready,
    new Promise((r) => setTimeout(() => r(null), ms)),
  ]);
}

// The VAPID key arrives as base64url; subscribe() wants raw bytes.
function keyBytes(base64url) {
  const padded = (base64url + '='.repeat((4 - (base64url.length % 4)) % 4))
    .replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(padded);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

/**
 * One of:
 *   'ios-needs-install' — iPhone/iPad, not installed: the ONE case with a fix
 *   'unsupported'       — this browser cannot do it at all
 *   'denied'            — permission refused; only site settings can undo it
 *   'on'                — this device is registered and will buzz
 *   'off'               — supported, not registered yet
 */
export async function pushState() {
  if (!pushSupported()) {
    return (isIOS() && !isStandalone()) ? 'ios-needs-install' : 'unsupported';
  }
  if (Notification.permission === 'denied') return 'denied';
  const reg = await registration();
  if (!reg) return 'unsupported';
  const sub = await reg.pushManager.getSubscription();
  if (!sub) return 'off';
  // The browser having a subscription is not proof WE still do (a restore, or a
  // key rotation, can leave the two out of step) — so ask.
  try {
    const { subscribed } = await api(`/push/status?endpoint=${encodeURIComponent(sub.endpoint)}`);
    return subscribed ? 'on' : 'off';
  } catch {
    return 'on';   // offline: trust the browser rather than claim it is off
  }
}

/** Ask permission and register this device. Returns the new state. */
export async function enablePush() {
  // Permission is asked from a TAP, and only once: a refusal is permanent and
  // cannot be re-prompted (P8).
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') return permission === 'denied' ? 'denied' : 'off';

  const reg = await registration();
  if (!reg) return 'unsupported';
  const { public_key: publicKey } = await api('/push/key');
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,               // required by Chrome, and honest anyway
    applicationServerKey: keyBytes(publicKey),
  });
  const json = sub.toJSON();
  await api('/push/subscribe', {
    body: { endpoint: json.endpoint, p256dh: json.keys.p256dh, auth: json.keys.auth },
  });
  return 'on';
}

/** Stop THIS device buzzing (others keep theirs). */
export async function disablePush() {
  const reg = await registration();
  const sub = reg && await reg.pushManager.getSubscription();
  if (!sub) return 'off';
  try { await api('/push/unsubscribe', { body: { endpoint: sub.endpoint } }); }
  finally { await sub.unsubscribe().catch(() => {}); }
  return 'off';
}
