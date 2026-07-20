"""SQLAlchemy ORM models — the schema source of truth.

The schema is defined here and evolved over time with Alembic migrations
(`alembic revision --autogenerate -m "..."` → review → `alembic upgrade head`).
The initial migration builds these tables; the app's runtime queries currently
run through the psycopg layer in app/db.py, but these models are the canonical
schema and are available for ORM queries.

Mirrors docs/design/DOMAIN.md. Note: Alembic autogenerate is reliable for
column/table changes but cannot always diff partial/expression indexes or CHECK
constraints — hand-check generated migrations for those (normal practice).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Text,
    TIMESTAMP,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Stable constraint/index names so Alembic autogenerate produces clean diffs.
_NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

TS = TIMESTAMP(timezone=True)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=_NAMING)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False)  # lowercased, private -- never in public shapes
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)  # bcrypt
    display_name: Mapped[str] = mapped_column(Text, nullable=False)  # public identity (1-60 chars, non-unique)
    bio: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    balance: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")  # cached ledger sum
    updated_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    __table_args__ = (
        CheckConstraint("balance >= 0", name="balance_nonneg"),
        Index("idx_users_email", text("lower(email)"), unique=True),
    )


class Session(Base):
    __tablename__ = "sessions"
    token: Mapped[str] = mapped_column(Text, primary_key=True)  # secrets.token_hex(32)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TS, nullable=False)  # now() + 30 days
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    __table_args__ = (Index("idx_sessions_user", "user_id"),)


class Project(Base):
    """The persistent SERVICE PROJECT. It has many events (occurrences).

    A project keeps its organizers (project_leaders), versioned waivers, images,
    and follows. The event-specific fields (schedule/location/code/status) live on
    ``events`` -- a project is now the durable umbrella, an event is one session.
    """
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    updated_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    __table_args__ = (
        Index("idx_projects_owner", "owner_id"),
    )


class Event(Base):
    """An occurrence of a service project -- a specific dated/located session.

    Owns what used to live on the project: ``starts_at``, ``expected_minutes``,
    ``location_text``, ``checkin_code`` (unique), and the ``open|completed``
    ``status``. ``is_over`` is a per-EVENT property (completed OR now() past
    starts_at + expected_minutes). participations and rsvps hang off an event.
    """
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    expected_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    location_text: Mapped[str] = mapped_column(Text, nullable=False)  # free text; geocoding deferred
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="open")
    checkin_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)  # secrets.token_urlsafe(6)
    updated_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    __table_args__ = (
        CheckConstraint("expected_minutes > 0", name="expected_minutes_pos"),
        CheckConstraint("status IN ('open', 'completed')", name="status_valid"),
        Index("idx_events_project", "project_id"),
        Index("idx_events_starts", "starts_at"),
    )


class ProjectLeader(Base):
    __tablename__ = "project_leaders"
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    __table_args__ = (Index("idx_leaders_user", "user_id"),)


class Rsvp(Base):
    __tablename__ = "rsvps"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    # Event-leader DESIGNATION -- a pure flag with no powers yet. Distinct from
    # project_leaders (organizers). Toggled by the organizer (am_leader).
    is_leader: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("event_id", "user_id", name="event_user"),
        Index("idx_rsvps_user", "user_id"),
    )


class Follow(Base):
    __tablename__ = "follows"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("user_id", "project_id", name="uq_follow"),
        Index("idx_follows_project", "project_id"),
    )


class Waiver(Base):
    __tablename__ = "waivers"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)  # immutable once created
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    __table_args__ = (UniqueConstraint("project_id", "version", name="project_version"),)


class Participation(Base):
    __tablename__ = "participations"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    waiver_id: Mapped[int] = mapped_column(ForeignKey("waivers.id"), nullable=False)  # the signed version
    checked_in_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    checked_out_at: Mapped[datetime | None] = mapped_column(TS)
    minutes: Mapped[int | None] = mapped_column(Integer)  # half-up elapsed
    tokens_awarded: Mapped[int | None] = mapped_column(Integer)  # from capped minutes
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    __table_args__ = (
        CheckConstraint("minutes >= 0", name="minutes_nonneg"),
        CheckConstraint("tokens_awarded >= 0", name="tokens_nonneg"),
        # One OPEN participation per (event, user); re-check-in after checkout is fine.
        Index("idx_participations_open", "event_id", "user_id",
              unique=True, postgresql_where=text("checked_out_at IS NULL")),
        Index("idx_participations_user", "user_id"),
        Index("idx_participations_event", "event_id"),
    )


class TokenEntry(Base):
    __tablename__ = "token_entries"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    from_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))  # NULL = system mint
    to_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    participation_id: Mapped[int | None] = mapped_column(ForeignKey("participations.id", ondelete="SET NULL"))
    claim_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_claims.id", ondelete="SET NULL"))
    catalog_item_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_items.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_pos"),
        CheckConstraint("kind IN ('earn', 'tip', 'spend')", name="kind_valid"),
        Index("idx_entries_to", "to_user_id", "id"),
        Index("idx_entries_from", "from_user_id", "id"),
    )


class CatalogItem(Base):
    __tablename__ = "catalog_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    poster_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    price_tokens: Mapped[int | None] = mapped_column(Integer)  # offers: required (0 ok); needs: NULL
    quantity: Mapped[int | None] = mapped_column(Integer)  # NULL = unlimited; reaches 0 -> auto-closed
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    updated_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    __table_args__ = (
        CheckConstraint("price_tokens >= 0", name="price_nonneg"),
        CheckConstraint("quantity >= 0", name="quantity_nonneg"),
        CheckConstraint("status IN ('active', 'closed')", name="status_valid"),
        CheckConstraint(
            "(kind = 'need' AND price_tokens IS NULL) OR (kind = 'offer' AND price_tokens IS NOT NULL)",
            name="kind_price",
        ),
        Index("idx_catalog_kind", "kind", "created_at"),
        Index("idx_catalog_poster", "poster_id"),
    )


class CatalogClaim(Base):
    __tablename__ = "catalog_claims"
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("catalog_items.id", ondelete="CASCADE"), nullable=False)
    claimant_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    price_tokens: Mapped[int] = mapped_column(Integer, nullable=False)  # snapshot at claim time
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(TS)
    __table_args__ = (
        CheckConstraint("price_tokens >= 0", name="price_nonneg"),
        CheckConstraint("status IN ('pending', 'accepted', 'declined', 'canceled')", name="status_valid"),
        # One live claim per (item, claimant).
        Index("idx_claims_pending", "item_id", "claimant_id",
              unique=True, postgresql_where=text("status = 'pending'")),
        Index("idx_claims_claimant", "claimant_id"),
        Index("idx_claims_item", "item_id"),
    )


class AuditLog(Base):
    """Append-only audit log — one immutable row per check-in / check-out.

    Written in the SAME tx as the state change it records (via app/audit.log),
    so a log row can never exist without its change or vice-versa. Rows are never
    updated or deleted; they are the source of truth for later reporting. Carries
    both the ``event_id`` (the occurrence) and the ``project_id`` (its umbrella,
    set to event.project_id when logging) for event- and project-level reporting.

    (Renamed from the old ``Event``/``events`` audit model — the name ``events``
    now belongs to the service-project occurrence table above.)
    """
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)  # 'check_in' | 'check_out'
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))  # who performed it
    subject_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))  # the volunteer it is about
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    event_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("events.id", ondelete="SET NULL"))
    participation_id: Mapped[int | None] = mapped_column(ForeignKey("participations.id", ondelete="SET NULL"))
    minutes: Mapped[int | None] = mapped_column(Integer)
    tokens: Mapped[int | None] = mapped_column(Integer)
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    __table_args__ = (
        Index("idx_audit_log_type", "type", "created_at"),
        Index("idx_audit_log_project", "project_id"),
        Index("idx_audit_log_subject", "subject_user_id"),
    )


class Image(Base):
    __tablename__ = "images"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entity: Mapped[str] = mapped_column(Text, nullable=False)  # 'project' | 'catalog_item'
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    __table_args__ = (
        CheckConstraint("entity IN ('project', 'catalog_item')", name="entity_valid"),
        CheckConstraint("content_type IN ('image/jpeg', 'image/png', 'image/webp')", name="content_type_valid"),
        CheckConstraint("size > 0 AND size <= 10485760", name="size_bounds"),  # 10 MB
        Index("idx_images_entity", "entity", "entity_id"),
    )
