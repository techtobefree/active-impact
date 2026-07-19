#!/usr/bin/env python3
"""Seed demo data for local UX exploration. DEV ONLY.

Refuses to run unless DATABASE_URL points at localhost. Creates a few users
(password: "password123"), a couple of impact projects with backdated,
checked-out participations (so wallets hold real earned tokens), and some
catalog offers/needs.

    python scripts/seed.py
"""
import os
import sys

from urllib.parse import urlsplit

# Make the repo root importable when run as `python scripts/seed.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Guard: never run against a non-local database.
url = os.environ.get("DATABASE_URL", "postgres://postgres:postgres@localhost:5433/impact")
host = urlsplit(url).hostname or ""
if host not in ("localhost", "127.0.0.1", "::1"):
    sys.exit(f"refusing to seed non-local DATABASE_URL (host={host!r})")

import secrets

from app import db, tokens
from app.auth import _hash_password


def user(email, display):
    row = db.query_one("SELECT * FROM users WHERE email=%s", (email,))
    if row:
        return row
    with db.tx() as c:
        return c.execute(
            "INSERT INTO users(email, password_hash, display_name, bio) "
            "VALUES (%s, %s, %s, %s) RETURNING *",
            (email, _hash_password("password123"), display,
             f"Demo account for {display}."),
        ).fetchone()


def project(owner, title, location, minutes, hours_ago_start=0):
    with db.tx() as c:
        p = c.execute(
            "INSERT INTO projects(owner_id, title, description, location_text, "
            "starts_at, expected_minutes, checkin_code) "
            "VALUES (%s, %s, %s, %s, now() - make_interval(hours => %s), %s, %s) RETURNING *",
            (owner["id"], title, f"Join us: {title}.", location,
             hours_ago_start, minutes, secrets.token_urlsafe(6)),
        ).fetchone()
        c.execute("INSERT INTO project_leaders(project_id, user_id) VALUES (%s, %s)",
                  (p["id"], owner["id"]))
        c.execute("INSERT INTO waivers(project_id, version, text) VALUES (%s, 1, %s)",
                  (p["id"], "I volunteer at my own risk. (Demo waiver.)"))
    return p


def volunteered(project_row, u, minutes_ago_in):
    """A completed participation: checked in `minutes_ago_in` ago, then out now."""
    with db.tx() as c:
        waiver = c.execute("SELECT id FROM waivers WHERE project_id=%s ORDER BY version DESC LIMIT 1",
                           (project_row["id"],)).fetchone()
        part = c.execute(
            "INSERT INTO participations(project_id, user_id, waiver_id, checked_in_at) "
            "VALUES (%s, %s, %s, now() - make_interval(mins => %s)) RETURNING *",
            (project_row["id"], u["id"], waiver["id"], minutes_ago_in),
        ).fetchone()
        # Reuse the real checkout math + mint.
        do_row = c.execute(
            "SELECT p.id, p.user_id, p.checked_in_at, pr.expected_minutes "
            "FROM participations p JOIN projects pr ON pr.id=p.project_id WHERE p.id=%s",
            (part["id"],),
        ).fetchone()
        tokens.do_checkout(c, dict(do_row))


def offer(poster, title, price, quantity=None):
    with db.tx() as c:
        c.execute(
            "INSERT INTO catalog_items(poster_id, kind, title, description, price_tokens, quantity) "
            "VALUES (%s, 'offer', %s, %s, %s, %s)",
            (poster["id"], title, f"{title} — available now.", price, quantity),
        )


def need(poster, title):
    with db.tx() as c:
        c.execute(
            "INSERT INTO catalog_items(poster_id, kind, title, description) "
            "VALUES (%s, 'need', %s, %s)",
            (poster["id"], title, f"Looking for help: {title}."),
        )


def main():
    db.init()
    ana = user("ana@example.com", "Ana Ortiz")
    ben = user("ben@example.com", "Ben Carter")
    mia = user("mia@example.com", "Mia Chen")

    park = project(ana, "Riverside Park Cleanup", "Riverside Park, Main St", 180, hours_ago_start=24)
    food = project(ben, "Community Food Drive", "St. Mark's Hall", 240, hours_ago_start=48)

    volunteered(park, ben, 125)   # ~2h -> 2 tokens
    volunteered(park, mia, 65)    # ~1h -> 1 token
    volunteered(food, ana, 190)   # ~3h -> 3 tokens
    volunteered(food, mia, 35)    # ~0.5h -> 1 token

    offer(mia, "Fresh sourdough (2 loaves)", 2, quantity=2)
    offer(ana, "1 hour of bike repair", 3)
    offer(ben, "50% off yoga class coupon", 1, quantity=5)
    need(mia, "Rides to the food bank on Saturdays")

    print("Seeded demo data. Users: ana@example.com / ben@example.com / "
          "mia@example.com  (password: password123)")
    for u in (ana, ben, mia):
        bal = db.query_one("SELECT balance FROM users WHERE id=%s", (u["id"],))["balance"]
        print(f"  {u['email']}: {bal} tokens")


if __name__ == "__main__":
    main()
