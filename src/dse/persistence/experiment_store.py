from sqlalchemy import Connection, Engine, insert, select

from dse.contracts.experiment import ExperimentManifest
from dse.engine.hashing import state_hash
from dse.persistence.schema import experiments


class ManifestConflictError(ValueError):
    """Raised when an experiment ID is reused with a different immutable manifest."""


def manifest_hash(manifest: ExperimentManifest) -> str:
    return state_hash(manifest.model_dump(mode="json"))


def register_experiment(engine: Engine, manifest: ExperimentManifest) -> str:
    expected_hash = manifest_hash(manifest)
    payload = manifest.model_dump(mode="json")

    with engine.begin() as connection:
        existing = _load_experiment_row(connection, manifest.experiment.id, lock=True)
        if existing is not None:
            if existing["manifest_hash"] != expected_hash:
                raise ManifestConflictError(
                    f"Experiment {manifest.experiment.id!r} already exists with another manifest"
                )
            return expected_hash

        connection.execute(
            insert(experiments).values(
                experiment_id=manifest.experiment.id,
                schema_version=manifest.schema_version,
                manifest=payload,
                manifest_hash=expected_hash,
            )
        )

    return expected_hash


def load_experiment_manifest(engine: Engine, experiment_id: str) -> ExperimentManifest:
    with engine.connect() as connection:
        row = _load_experiment_row(connection, experiment_id, lock=False)

    if row is None:
        raise KeyError(f"Unknown experiment: {experiment_id}")

    return ExperimentManifest.model_validate(row["manifest"])


def _load_experiment_row(
    connection: Connection,
    experiment_id: str,
    *,
    lock: bool,
):
    statement = select(experiments).where(experiments.c.experiment_id == experiment_id)
    if lock:
        statement = statement.with_for_update()
    return connection.execute(statement).mappings().one_or_none()
