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
                    **report.model_dump(mode="json")
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
        return FunctionalEvaluationReport.model_validate(
            {
                field: row[field]
                for field in FunctionalEvaluationReport.model_fields
            }
        )
