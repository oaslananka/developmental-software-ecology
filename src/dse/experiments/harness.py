from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

from dse.contracts.experiment import ExperimentManifest
from dse.experiments.accounting import (
    assert_compute_budget_matched,
)
from dse.experiments.conditions import (
    ConditionSupportReport,
    assess_condition_support,
    validate_condition_manifest,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConditionHarnessReport(StrictModel):
    compute_match_group: str
    budget_fingerprint_hash: str
    conditions: tuple[str, ...]
    support: tuple[ConditionSupportReport, ...]
    all_research_runtime_ready: bool


def validate_condition_harness(
    manifests: Iterable[ExperimentManifest],
) -> ConditionHarnessReport:
    items = list(manifests)
    if not items:
        raise ValueError("Condition harness requires at least one manifest")

    condition_names = [
        manifest.experiment.condition
        for manifest in items
    ]
    if len(condition_names) != len(set(condition_names)):
        raise ValueError("Condition harness cannot contain duplicate conditions")

    groups = {
        manifest.study.compute_match_group
        for manifest in items
    }
    if None in groups or len(groups) != 1:
        raise ValueError(
            "Condition harness requires one non-null compute_match_group"
        )

    for manifest in items:
        if not manifest.study.enforce_condition_contract:
            raise ValueError(
                f"{manifest.experiment.id} does not enforce the condition contract"
            )
        validate_condition_manifest(manifest)

    budget_hash = (
        assert_compute_budget_matched(items)
        if len(items) >= 2
        else ""
    )
    support = tuple(
        assess_condition_support(manifest)
        for manifest in items
    )

    return ConditionHarnessReport(
        compute_match_group=next(iter(groups)),
        budget_fingerprint_hash=budget_hash,
        conditions=tuple(sorted(condition_names)),
        support=support,
        all_research_runtime_ready=all(
            item.research_runtime_ready
            for item in support
        ),
    )
