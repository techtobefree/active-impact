"""M0: the skeleton boots, serves the shell, and the schema is idempotent."""


def test_health_ok(api):
    r = api.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "db": True}


def test_static_shell_served(api):
    r = api.get("/")
    assert r.status_code == 200
    assert "Active Impact" in r.text


def test_manifest_served(api):
    r = api.get("/manifest.webmanifest")
    assert r.status_code == 200


def test_schema_is_idempotent():
    # Re-applying the whole schema must never error (runs on every boot).
    from app import db
    db.init()
    db.init()
