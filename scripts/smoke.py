#!/usr/bin/env python3
"""End-to-end smoke test for a running Active Impact instance.

Stdlib only -- runs anywhere Python 3 exists, including inside the app container:
    python scripts/smoke.py https://your-domain
    docker compose -f docker-compose.prod.yml exec app python scripts/smoke.py http://localhost:8000

Walks the real happy paths and asserts each step. Creates throwaway users
(smoke-<epoch>-a/b) -- harmless on a fresh instance. Exits non-zero on any
failure and prints where it broke. Prints "SMOKE PASS" on success.
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
STAMP = int(time.time())
A = f"smoke-{STAMP}-a"
B = f"smoke-{STAMP}-b"
PW = "smoke-password-123"


def call(method, path, token=None, body=None, raw=False):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        payload = e.read()
        status = e.code
    if raw:
        return status, payload
    try:
        return status, (json.loads(payload) if payload else None)
    except json.JSONDecodeError:
        return status, payload.decode(errors="replace")


def check(cond, msg, got=None):
    if not cond:
        print(f"SMOKE FAIL: {msg}" + (f"  (got: {got!r})" if got is not None else ""))
        sys.exit(1)
    print(f"  ok: {msg}")


print(f"Active Impact smoke test against {BASE}")

# 1. health
s, j = call("GET", "/api/health")
check(s == 200 and j.get("ok") and j.get("db"), "health ok + db", (s, j))

# 2. register a & b; login a
s, j = call("POST", "/api/auth/register", body={"username": A, "password": PW})
check(s == 201 and j.get("token"), "register A", (s, j))
tok_a = j["token"]
s, j = call("POST", "/api/auth/register", body={"username": B, "password": PW})
check(s == 201, "register B", s)
tok_b = j["token"]
s, j = call("POST", "/api/auth/login", body={"username": A, "password": PW})
check(s == 200 and j.get("token"), "login A", (s, j))
tok_a = j["token"]

# 3. a creates a project and reads it back as leader
s, j = call("POST", "/api/projects", token=tok_a, body={
    "title": "Smoke Park Cleanup", "location_text": "Riverside Park",
    "starts_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "expected_minutes": 120,
})
check(s == 201 and j.get("id"), "create project", (s, j))
pid = j["id"]
s, j = call("GET", f"/api/projects/{pid}", token=tok_a)
check(s == 200 and j.get("am_leader") is True, "project detail: am_leader", (s, j))
check(bool(j.get("waiver", {}).get("text")), "waiver v1 present", j.get("waiver"))
code = j.get("checkin_code")
check(bool(code), "checkin_code exposed to leader", j)
s, raw = call("GET", f"/api/projects/{pid}/qr.svg", token=tok_a, raw=True)
check(s == 200 and b"<svg" in raw, "qr.svg is SVG", s)

# 4. b resolves the code, agrees, cannot double-agree
s, j = call("GET", f"/api/checkin/{code}", token=tok_b)
check(s == 200 and j.get("project", {}).get("id") == pid, "resolve checkin code", (s, j))
s, j = call("POST", f"/api/checkin/{code}/agree", token=tok_b)
check(s == 201 and j.get("id"), "b agrees (checks in)", (s, j))
s, j = call("POST", f"/api/checkin/{code}/agree", token=tok_b)
check(s == 409 and j.get("detail") == "already_checked_in", "duplicate agree 409", (s, j))

# 5. a checks b out via the roster (rows carry participation id)
s, roster = call("GET", f"/api/projects/{pid}/roster", token=tok_a)
parts = roster.get("participations") if isinstance(roster, dict) else roster
check(s == 200 and parts, "roster non-empty", (s, roster))
part_id = parts[0]["id"]
s, j = call("POST", f"/api/participations/{part_id}/checkout", token=tok_a)
check(s == 200 and j.get("tokens_awarded") == 0, "checkout b -> 0 tokens (near-zero time)", (s, j))

# 6. b has no tokens -> tip fails with the ledger guard
s, j = call("POST", "/api/tokens/tip", token=tok_b, body={"to_username": A, "amount": 1})
check(s == 409 and j.get("detail") == "insufficient_balance", "tip with empty wallet 409", (s, j))

# 7. a posts a free offer; b claims; a accepts
s, j = call("POST", "/api/catalog", token=tok_a, body={
    "kind": "offer", "title": "Free smoke muffins", "price_tokens": 0})
check(s == 201 and j.get("id"), "post free offer", (s, j))
item_id = j["id"]
s, j = call("POST", f"/api/catalog/{item_id}/claim", token=tok_b)
check(s == 201 and j.get("id"), "b claims offer", (s, j))
claim_id = j["id"]
s, j = call("POST", f"/api/claims/{claim_id}/accept", token=tok_a)
check(s == 200 and j.get("status") == "accepted", "a accepts claim", (s, j))

# 8. auth wall
s, j = call("GET", "/api/me")
check(s == 401, "unauthenticated /api/me is 401", (s, j))

print("SMOKE PASS")
