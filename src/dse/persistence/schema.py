from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, MetaData, Table, Text
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


functional_evaluations = Table(
    "functional_evaluations",
    metadata,
    Column(
        "experiment_id",
        Text,
        ForeignKey("experiments.experiment_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("evaluation_id", Text, primary_key=True),
    Column("condition", Text, nullable=False),
    Column("world_tick", BigInteger, nullable=False),
    Column("world_sequence", BigInteger, nullable=False),
    Column("world_snapshot_hash", Text, nullable=False),
    Column("culture_snapshot_hash", Text, nullable=False),
    Column("opportunity_spec_id", Text, nullable=False),
    Column("opportunity_spec_hash", Text, nullable=False),
    Column("opportunity_aggregation", Text, nullable=False),
    Column("suite_id", Text, nullable=False),
    Column("suite_hash", Text, nullable=False),
    Column("artifact_bindings", JSONB, nullable=False),
    Column("passed_cases", BigInteger, nullable=False),
    Column("failed_cases", BigInteger, nullable=False),
    Column("total_cases", BigInteger, nullable=False),
    Column("functional_score", Float, nullable=False),
    Column("duration_ms", BigInteger, nullable=False),
    Column("policy_hash", Text, nullable=False),
    Column("attestation_id", Text, nullable=False),
    Column("attestation_evidence_kind", Text, nullable=False),
    Column("backend", Text, nullable=False),
    Column("backend_version", Text, nullable=False),
    Column("runner_kind", Text, nullable=False),
    Column("runner_version", Text, nullable=False),
    Column("worker_build_sha256", Text, nullable=False),
    Column("runtime_build_sha256", Text, nullable=False),
    Column("result_hash", Text, nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)
