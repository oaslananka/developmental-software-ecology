import asyncio
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from dse.contracts.experiment import ExperimentManifest, load_manifest
from dse.engine.forge_actions import advance_forge_action_runtime_ticks
from dse.engine.structured_cognition import advance_structured_cognition_ticks
from dse.engine.world import create_world
from dse.experiments.accounting import (
    assert_compute_budget_matched,
    compare_observed_compute,
    compute_usage,
)
from dse.experiments.conditions import (
    assess_condition_support,
    expected_condition_profile,
)
from dse.experiments.harness import validate_condition_harness
from dse.forge.deterministic import DeterministicMemoryForgeProvider
from dse.providers.fake import DeterministicFakeProvider


CONDITION_PATHS = {
    "P": Path("experiments/v0_1/conditions/p-personal.yaml"),
    "T": Path("experiments/v0_1/conditions/t-text-culture.yaml"),
    "E": Path("experiments/v0_1/conditions/e-executable-culture.yaml"),
    "ES": Path("experiments/v0_1/conditions/es-executable-social.yaml"),
    "RIL": Path("experiments/v0_1/conditions/ril-control.yaml"),
}
M13_MANIFEST = Path("experiments/v0_1/controlled-turnover-fake.yaml")


def _manifests() -> dict[str, ExperimentManifest]:
    return {
        condition: load_manifest(path)
        for condition, path in CONDITION_PATHS.items()
    }


def test_condition_profiles_preserve_research_matrix() -> None:
    expected = {
        "P": ("none", "none", "limited", True, False),
        "T": ("persistent", "none", "direct", True, False),
        "E": ("docs_only", "executable", "none", True, False),
        "ES": (
            "persistent",
            "executable",
            "issues_pr_messages",
            True,
            False,
        ),
        "RIL": ("none", "none", "isolated", False, True),
    }

    for condition, values in expected.items():
        profile = expected_condition_profile(condition)
        assert (
            profile.text_culture,
            profile.artifact_culture,
            profile.social_channel,
            profile.turnover_required,
            profile.ril_control,
        ) == values


def test_all_primary_condition_manifests_share_compute_budget() -> None:
    manifests = _manifests()

    fingerprint_hash = assert_compute_budget_matched(
        manifests.values()
    )

    assert len(fingerprint_hash) == 64
    assert {
        manifest.study.compute_match_group
        for manifest in manifests.values()
    } == {"v0_1-primary-compute"}

    for manifest in manifests.values():
        assert manifest.world.agent_count == 5
        assert manifest.agents.turnover.enabled is True
        assert manifest.agents.turnover.ticks == [150]
        assert manifest.agents.cognition.model_calls_per_cycle == 7
        assert manifest.agents.cognition.interval_ticks == 20
        assert manifest.agents.actions.proposals_per_cycle == 4
        assert manifest.agents.memory.capacity == 32
        assert manifest.runtime.forge.operations_per_cycle == 4
        assert manifest.runtime.text_culture.operations_per_cycle == 4
        assert manifest.runtime.social.operations_per_cycle == 4
        assert manifest.evaluation.enabled is True
        assert manifest.evaluation.suite_id == "v0_1-hidden-functional-suite"
        assert (
            manifest.evaluation.suite_hash
            == "4791850327e4f4fead67e668c6a4d7e155048653b8cabf5fdede47c31b91f8a4"
        )
        assert manifest.evaluation.sandbox_enabled is True
        assert manifest.evaluation.sandbox_policy.backend == "external-hardened"


def test_condition_contract_rejects_treatment_drift() -> None:
    raw = yaml.safe_load(
        CONDITION_PATHS["P"].read_text(encoding="utf-8")
    )
    raw["study"]["text_culture"] = "persistent"

    with pytest.raises(
        ValidationError,
        match="Condition treatment mismatch",
    ):
        ExperimentManifest.model_validate(raw)

    raw = yaml.safe_load(
        CONDITION_PATHS["P"].read_text(encoding="utf-8")
    )
    raw["runtime"]["forge_enabled"] = True

    with pytest.raises(
        ValidationError,
        match="runtime.forge_enabled",
    ):
        ExperimentManifest.model_validate(raw)

    raw = yaml.safe_load(
        CONDITION_PATHS["T"].read_text(encoding="utf-8")
    )
    raw["runtime"]["social"]["enabled"] = False

    with pytest.raises(
        ValidationError,
        match="runtime.social.enabled",
    ):
        ExperimentManifest.model_validate(raw)


def test_evaluation_profile_roundtrips_canonical_manifest() -> None:
    manifest = load_manifest(CONDITION_PATHS["T"])

    restored = ExperimentManifest.model_validate(
        manifest.model_dump(mode="json")
    )

    assert restored == manifest
    assert restored.evaluation_profile == "v0_1-hidden-functional-suite"


def test_evaluation_profile_rejects_expanded_config_drift() -> None:
    manifest = load_manifest(CONDITION_PATHS["E"])
    raw = manifest.model_dump(mode="json")
    raw["evaluation"]["sandbox_policy"]["memory_mb"] = 512

    with pytest.raises(
        ValidationError,
        match="evaluation does not match evaluation_profile",
    ):
        ExperimentManifest.model_validate(raw)


def test_ril_requires_explicit_isolated_topology() -> None:
    raw = yaml.safe_load(
        CONDITION_PATHS["RIL"].read_text(encoding="utf-8")
    )
    raw["study"]["ril_topology"] = "not_applicable"

    with pytest.raises(
        ValidationError,
        match="RIL condition requires",
    ):
        ExperimentManifest.model_validate(raw)


def test_legacy_manifests_remain_opt_out_compatible() -> None:
    manifest = load_manifest(M13_MANIFEST)

    assert manifest.study.enforce_condition_contract is False
    assert manifest.study.compute_match_group is None


def test_support_gate_reports_missing_runtime_surfaces_without_theater() -> None:
    manifests = _manifests()
    reports = {
        condition: assess_condition_support(manifest)
        for condition, manifest in manifests.items()
    }

    assert reports["P"].research_runtime_ready is True
    assert reports["P"].missing_surfaces == []
    assert any(
        "limited communication" in note
        for note in reports["P"].notes
    )

    assert reports["RIL"].research_runtime_ready is True
    assert reports["RIL"].missing_surfaces == []

    assert reports["T"].research_runtime_ready is True
    assert reports["T"].missing_surfaces == []

    assert reports["E"].research_runtime_ready is False
    assert reports["E"].missing_surfaces == [
        "attested_hardened_evaluator_runtime",
    ]

    assert reports["ES"].research_runtime_ready is False
    assert reports["ES"].missing_surfaces == [
        "attested_hardened_evaluator_runtime",
    ]


def test_harness_reports_full_matrix_but_does_not_claim_false_readiness() -> None:
    report = validate_condition_harness(_manifests().values())

    assert report.compute_match_group == "v0_1-primary-compute"
    assert len(report.budget_fingerprint_hash) == 64
    assert report.conditions == ("E", "ES", "P", "RIL", "T")
    assert report.all_research_runtime_ready is False

    support = {
        item.condition: item
        for item in report.support
    }
    assert support["P"].research_runtime_ready is True
    assert support["RIL"].research_runtime_ready is True
    assert support["E"].research_runtime_ready is False
    assert support["ES"].research_runtime_ready is False
    assert support["T"].research_runtime_ready is True


def test_compute_budget_drift_is_rejected_even_if_treatment_is_valid() -> None:
    manifests = _manifests()
    ril_raw = yaml.safe_load(
        CONDITION_PATHS["RIL"].read_text(encoding="utf-8")
    )
    ril_raw["experiment"]["id"] = "v0_1-condition-ril-drifted"
    ril_raw["agents"]["cognition"]["model_calls_per_cycle"] = 8
    drifted = ExperimentManifest.model_validate(ril_raw)

    with pytest.raises(
        ValueError,
        match="Compute budget fingerprint mismatch",
    ):
        assert_compute_budget_matched(
            [
                manifests["P"],
                drifted,
            ]
        )


def test_m13_event_stream_has_explicit_compute_accounting() -> None:
    manifest = load_manifest(M13_MANIFEST)
    world = create_world(manifest)

    events = asyncio.run(
        advance_forge_action_runtime_ticks(
            world,
            manifest,
            DeterministicFakeProvider(),
            DeterministicMemoryForgeProvider(),
            280,
        )
    )
    usage = compute_usage(events)

    assert usage.final_world_tick == 280
    assert usage.model_calls == 70
    assert usage.input_tokens == 2240
    assert usage.output_tokens == 840
    assert usage.total_tokens == 3080
    assert usage.action_proposals == 40
    assert usage.tool_executions == 0
    assert usage.forge_operations == 40
    assert usage.turnovers == 5
    assert usage.repositories_created == 10
    assert usage.artifacts_committed == 20


def test_p_and_ril_observed_compute_match_under_equal_budget() -> None:
    manifests = _manifests()
    usages = {}

    for condition in ("P", "RIL"):
        manifest = manifests[condition]
        world = create_world(manifest)
        events = asyncio.run(
            advance_structured_cognition_ticks(
                world,
                manifest,
                DeterministicFakeProvider(),
                280,
            )
        )
        usages[condition] = compute_usage(events)

    report = compare_observed_compute(usages)

    assert report.matched is True
    assert report.differences == {}
    assert usages["P"].model_calls == 70
    assert usages["RIL"].model_calls == 70
    assert usages["P"].total_tokens == 3080
    assert usages["RIL"].total_tokens == 3080
    assert usages["P"].action_proposals == 40
    assert usages["RIL"].action_proposals == 40
    assert usages["P"].forge_operations == 0
    assert usages["RIL"].forge_operations == 0
