import os
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, String, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./robotops.db")
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
    if DATABASE_URL.startswith("sqlite")
    else {},
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def now():
    return datetime.now(UTC)


def uid():
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str]


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(index=True)
    name: Mapped[str]


class Scenario(Base):
    __tablename__ = "scenarios"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(index=True)
    project_id: Mapped[str] = mapped_column(index=True)
    name: Mapped[str]
    severity: Mapped[str] = mapped_column(default="medium")
    description: Mapped[str] = mapped_column(default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    steps: Mapped[list] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(default=1)


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(index=True)
    name: Mapped[str]
    simulated: Mapped[bool] = mapped_column(default=True)
    online: Mapped[bool] = mapped_column(default=True)
    last_seen: Mapped[datetime | None] = mapped_column(default=None)
    # Device credential: only a SHA-256 hash is stored; the plaintext key is shown once on create/rotate.
    key_hash: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, default=None
    )
    key_prefix: Mapped[str | None] = mapped_column(String(16), default=None)
    key_rotated_at: Mapped[datetime | None] = mapped_column(default=None)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("device_id", "event_id"),
    )  # idempotent ingestion
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(index=True)
    device_id: Mapped[str] = mapped_column(index=True)
    event_id: Mapped[str]
    correlation_id: Mapped[str] = mapped_column(index=True)
    type: Mapped[str]
    ts_ms: Mapped[int]
    payload: Mapped[dict] = mapped_column(JSON)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(index=True)
    scenario_id: Mapped[str] = mapped_column(index=True)
    scenario_snapshot: Mapped[dict] = mapped_column(JSON)
    events: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(index=True)
    findings: Mapped[list] = mapped_column(JSON)
    duration_ms: Mapped[int]
    replay_of: Mapped[str | None] = mapped_column(default=None)
    project_id: Mapped[str | None] = mapped_column(index=True, default=None)
    device_id: Mapped[str | None] = mapped_column(index=True, default=None)
    correlation_id: Mapped[str | None] = mapped_column(index=True, default=None)
    logs: Mapped[list | None] = mapped_column(
        JSON, default=None
    )  # per-run structured log entries
    device_state: Mapped[dict | None] = mapped_column(
        JSON, default=None
    )  # device snapshot when the run was recorded
    seeded: Mapped[bool] = mapped_column(
        default=False
    )  # demo data, not a real measurement
    created_at: Mapped[datetime] = mapped_column(index=True, default=now)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(index=True)
    action: Mapped[str]
    target: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime] = mapped_column(default=now)


class DeliveryFailure(Base):
    __tablename__ = "delivery_failures"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(index=True)
    device_id: Mapped[str] = mapped_column(index=True)
    event_id: Mapped[str]
    reason: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(
        index=True, default="failed"
    )  # failed | delivered
    created_at: Mapped[datetime] = mapped_column(default=now)


class SimulationJob(Base):
    __tablename__ = "simulation_jobs"
    id: Mapped[str] = mapped_column(primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(index=True)
    device_id: Mapped[str] = mapped_column(index=True)
    scenario_id: Mapped[str]
    fail: Mapped[bool] = mapped_column(default=False)
    delay_ms: Mapped[int] = mapped_column(
        default=0
    )  # real pause between emitted events (0 = as fast as possible)
    auto_run: Mapped[bool] = mapped_column(default=True)
    correlation_id: Mapped[str] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(
        index=True, default="queued"
    )  # queued|running|completed|failed|cancelled
    events_total: Mapped[int] = mapped_column(default=0)
    events_emitted: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(default=None)
    run_id: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(index=True, default=now)
    # Lease: the claiming worker's token + heartbeat. Only jobs with a stale heartbeat are recovered,
    # and a worker that loses its lease (recovered or cancelled) stops at its next heartbeat.
    lease_token: Mapped[str | None] = mapped_column(String(32), default=None)
    heartbeat_at: Mapped[datetime | None] = mapped_column(default=None)
