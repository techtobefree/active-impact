// In-app QR scanner — the camera half of the peer check-in (CHECKIN_PROOF.md §7).
//
// Platform APIs only: `BarcodeDetector` + `getUserMedia`. No library, no build
// step, no new dependency (D4). BarcodeDetector is Chromium-only today, so
// EVERY caller must handle {unavailable} — and that path is not a failure, it is
// the documented fallback to the asserted check-in. On a browser without it the
// native camera still works, because our QR codes are plain URLs (D5).
//
// scanQR() resolves to exactly one of:
//   { text }               a code was read
//   { cancelled: true }    the user backed out — a DECISION, do nothing
//   { unavailable: true }  no scanner here — fall back
import { el } from './ui.js';

export function scannerSupported() {
  return typeof window.BarcodeDetector === 'function'
    && !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
}

// Does this engine actually decode QR (it may ship BarcodeDetector with a
// different format set)? Async, so it is checked inside scanQR, not by callers.
async function qrSupported() {
  try {
    const formats = await window.BarcodeDetector.getSupportedFormats();
    return formats.includes('qr_code');
  } catch {
    return false;
  }
}

// A scanned string -> the in-app route to go to, or null if it isn't ours.
// Accepts a full URL or a bare hash, and handles BOTH kinds of Active Impact
// code: a person's (#/s/{qr_token}/{event_id}) and an event's (#/c/{code}) —
// somebody pointing this scanner at the poster on the wall should just work.
export function parseScan(text) {
  if (!text) return null;
  const hash = String(text).trim().replace(/^[^#]*#/, '#');
  if (/^#\/s\/[\w-]+\/\d+$/.test(hash)) return hash;
  if (/^#\/c\/[\w-]+$/.test(hash)) return hash;
  return null;
}

export async function scanQR() {
  if (!scannerSupported() || !(await qrSupported())) return { unavailable: true };

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: 'environment' } },
      audio: false,
    });
  } catch {
    // No camera, or permission denied/dismissed. Both mean "can't scan here".
    return { unavailable: true };
  }

  const overlay = el('<div class="scanner"></div>');
  const video = el('<video autoplay playsinline muted></video>');
  const frame = el('<div class="scanner-frame"></div>');
  const hint = el('<p class="scanner-hint">Point at someone\'s Active Impact code</p>');
  const cancel = el('<button class="act block big">Cancel</button>');
  overlay.append(video, frame, hint, cancel);
  document.body.append(overlay);

  video.srcObject = stream;
  try { await video.play(); } catch { /* autoplay attrs normally cover this */ }

  const detector = new window.BarcodeDetector({ formats: ['qr_code'] });

  return new Promise((resolve) => {
    let raf = null;
    let done = false;
    const finish = (result) => {
      if (done) return;
      done = true;
      if (raf) cancelAnimationFrame(raf);
      stream.getTracks().forEach((t) => t.stop());
      overlay.remove();
      resolve(result);
    };

    cancel.onclick = () => finish({ cancelled: true });

    const tick = async () => {
      if (done) return;
      try {
        const codes = await detector.detect(video);
        const hit = codes.find((c) => c.rawValue);
        if (hit) { finish({ text: hit.rawValue }); return; }
      } catch {
        // A transient decode error (e.g. a not-yet-sized video frame) is normal.
        // Keep looping; a genuinely dead camera just never resolves until Cancel.
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
  });
}
