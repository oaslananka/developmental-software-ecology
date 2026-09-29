from sqlalchemy import BigInteger, DateTime, ForeignKey, MetaData, Table, Text
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func


metadata = MetaData()

experiments = Table(
    "experiments",
    metadata,
    Column("experiment_id", Text, primary_key=True),
    Column("schema_version", Text, nullable=False),
    Column("manifest", JSONB, nullable=False),
    Column("manifest_hash", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

world_events = Table(
    "world_events",
    metadata,
    Column(
        "experiment_id",
        Text,
        ForeignKey("experiments.experiment_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("sequence_number", BigInteger, primary_key=True),
    Column("event_id", Text, nullable=False, unique=True),
    Column("world_tick", BigInteger, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("actor_agent_id", Text, nullable=True),
    Column("causal_parent_event_id", Text, nullable=True),
    Column("correlation_id", Text, nullable=True),
    Column("payload", JSONB, nullable=False),
    Column("created_at_wall_clock", DateTime(timezone=True), nullable=False),
)

world_snapshots = Table(
    "world_snapshots",
    metadata,
    Column(
        "experiment_id",
        Text,
        ForeignKey("experiments.experiment_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("last_sequence_number", BigInteger, primary_key=True),
    Column("world_tick", BigInteger, nullable=False),
    Column("state", JSONB, nullable=False),
    Column("state_hash", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
