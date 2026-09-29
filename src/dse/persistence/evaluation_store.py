from sqlalchemy import Engine, insert, select

from dse.contracts.evaluation import FunctionalEvaluationReport
from dse.engine.hidden_evaluator import functional_evaluation_report_hash
from dse.persistence.schema import functional_evaluations


class EvaluationConflictError(ValueError):
    """Raised when one evaluation ID is reused with different result bytes."""


class EvaluationIntegrityError(ValueError):
    """Raised when a persisted evaluation report hash does not validate."""


class PostgresEvaluationStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save(
        self,
        report: FunctionalEvaluationReport,
    ) -> FunctionalEvaluationReport:
        expected_hash = functional_evaluation_report_hash(report)
        if report.result_hash != expected_hash:
            raise EvaluationIntegrityError(
                "evaluation report result_hash does not match report fields"
            )

        with self._engine.begin() as connection:
            existing = (
                connection.execute(
                    select(functional_evaluations).where(
                        functional_evaluations.c.experiment_id
                        == report.experiment_id,
                        functional_evaluations.c.evaluation_id
                        == report.evaluation_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if existing["result_hash"] != report.result_hash:
                    raise EvaluationConflictError(
                        f"Evaluation {report.evaluation_id!r} already exists "
                        "with a different result"
                    )
                return self._report_from_row(existing)

            connection.execute(
                insert(functional_evaluations).values(
                    experiment_id=report.experiment_id,
                    evaluation_id=report.evaluation_id,
                    condition=report.condition,
                    world_tick=report.world_tick,
                    world_sequence=report.world_sequence,
                    world_snapshot_hash=report.world_snapshot_hash,
                    culture_snapshot_hash=report.culture_snapshot_hash,
                    suite_id=report.suite_id,
                    suite_hash=report.suite_hash,
                    artifact_bindings=[
                        binding.model_dump(mode="json")
                        for binding in report.artifact_bindings
                    ],
                    passed_cases=report.passed_cases,
                    failed_cases=report.failed_cases,
                    total_cases=report.total_cases,
                    functional_score=report.functional_score,
                    duration_ms=report.duration_ms,
                    policy_hash=report.policy_hash,
                    attestation_id=report.attestation_id,
                    backend=report.backend,
                    backend_version=report.backend_version,
                    runner_kind=report.runner_kind,
                    runner_version=report.runner_version,
                    result_hash=report.result_hash,
                )
            )

        return report

    def load_latest(
        self,
        experiment_id: str,
    ) -> FunctionalEvaluationReport | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(functional_evaluations)
                    .where(
                        functional_evaluations.c.experiment_id
                        == experiment_id
                    )
                    .order_by(
                        functional_evaluations.c.world_sequence.desc(),
                        functional_evaluations.c.evaluation_id.desc(),
                    )
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )

        if row is None:
            return None
        return self._validated_report(row)

    def load_all(
        self,
        experiment_id: str,
    ) -> list[FunctionalEvaluationReport]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    select(functional_evaluations)
                    .where(
                        functional_evaluations.c.experiment_id
                        == experiment_id
                    )
                    .order_by(
                        functional_evaluations.c.world_sequence,
                        functional_evaluations.c.evaluation_id,
                    )
                )
                .mappings()
                .all()
            )

        return [self._validated_report(row) for row in rows]

    def _validated_report(self, row) -> FunctionalEvaluationReport:
        report = self._report_from_row(row)
        actual_hash = functional_evaluation_report_hash(report)
        if actual_hash != report.result_hash:
            raise EvaluationIntegrityError(
                f"Persisted evaluation hash mismatch: "
                f"expected {report.result_hash}, got {actual_hash}"
            )
        return report

    @staticmethod
    def _report_from_row(row) -> FunctionalEvaluationReport:
        return FunctionalEvaluationReport(
            evaluation_id=row["evaluation_id"],
            experiment_id=row["experiment_id"],
            condition=row["condition"],
            world_tick=row["world_tick"],
            world_sequence=row["world_sequence"],
            world_snapshot_hash=row["world_snapshot_hash"],
            culture_snapshot_hash=row["culture_snapshot_hash"],
            suite_id=row["suite_id"],
            suite_hash=row["suite_hash"],
            artifact_bindings=row["artifact_bindings"],
            passed_cases=row["passed_cases"],
            failed_cases=row["failed_cases"],
            total_cases=row["total_cases"],
            functional_score=row["functional_score"],
            duration_ms=row["duration_ms"],
            policy_hash=row["policy_hash"],
            attestation_id=row["attestation_id"],
            backend=row["backend"],
            backend_version=row["backend_version"],
            runner_kind=row["runner_kind"],
            runner_version=row["runner_version"],
            result_hash=row["result_hash"],
        )
