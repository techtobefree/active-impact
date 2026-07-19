// API client + session storage. One fetch helper with centralized 401 handling.
const TOKEN_KEY = 'ai_token';
const USER_KEY = 'ai_user';
const RETURN_KEY = 'ai_return';

export function getToken() { return localStorage.getItem(TOKEN_KEY); }
export function currentUser() {
  try { return JSON.parse(localStorage.getItem(USER_KEY)); } catch { return null; }
}
export function setSession(token, user) {
  localStorage.setItem(TOKEN_KEY, token);
  if (user) localStorage.setItem(USER_KEY, JSON.stringify(user));
}
export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}
export function stashReturn(hash) { sessionStorage.setItem(RETURN_KEY, hash || location.hash || '#/'); }
export function popReturn() {
  const r = sessionStorage.getItem(RETURN_KEY);
  sessionStorage.removeItem(RETURN_KEY);
  return r && r !== '#/login' && r !== '#/register' ? r : '#/';
}

// api(path, {method, body}) -> parsed JSON (or null on 204).
// Throws {status, detail} on non-2xx, {offline:true} on network failure.
// A 401 clears the session, stashes the current route, and routes to #/login.
export async function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json' };
  const tok = getToken();
  if (tok) headers['Authorization'] = 'Bearer ' + tok;
  let res;
  try {
    res = await fetch('/api' + path, {
      method: opts.method || (opts.body ? 'POST' : 'GET'),
      headers: { ...headers, ...(opts.headers || {}) },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw { offline: true, detail: 'offline' };
  }
  if (res.status === 401) {
    clearSession();
    stashReturn();
    if (location.hash !== '#/login') location.hash = '#/login';
    throw { status: 401, detail: 'unauthorized' };
  }
  if (res.status === 204) return null;
  const ct = res.headers.get('content-type') || '';
  const data = ct.includes('json') ? await res.json() : await res.text();
  if (!res.ok) throw { status: res.status, detail: (data && data.detail) || 'error' };
  return data;
}

// Fetch an authed binary (image) and return an object URL. Caller revokes it.
export async function apiBlobURL(path) {
  const tok = getToken();
  const res = await fetch('/api' + path, { headers: tok ? { Authorization: 'Bearer ' + tok } : {} });
  if (!res.ok) throw { status: res.status };
  return URL.createObjectURL(await res.blob());
}
