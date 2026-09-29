from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from dse.contracts.evaluation import FunctionalEvaluationReport
    from dse.contracts.experiment import ExperimentManifest

from dse.engine.hashing import state_hash


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConditionProfile(StrictModel):
    condition: Literal["P", "T", "E", "ES", "RIL"]
    text_culture: Literal["none", "persistent", "docs_only"]
    artifact_culture: Literal["none", "executable"]
    social_channel: Literal[
        "none",
        "limited",
        "direct",
        "issues_pr_messages",
        "isolated",
    ]
    turnover_required: bool
    ril_control: bool


class ConditionSupportReport(StrictModel):
    condition: Literal["P", "T", "E", "ES", "RIL"]
    contract_valid: bool = True
    research_runtime_ready: bool
    missing_surfaces: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


_PROFILES: dict[str, ConditionProfile] = {
    "P": ConditionProfile(
        condition="P",
        text_culture="none",
        artifact_culture="none",
        social_channel="limited",
        turnover_required=True,
        ril_control=False,
    ),
    "T": ConditionProfile(
        condition="T",
        text_culture="persistent",
        artifact_culture="none",
        social_channel="direct",
        turnover_required=True,
        ril_control=False,
    ),
    "E": ConditionProfile(
        condition="E",
        text_culture="docs_only",
        artifact_culture="executable",
        social_channel="none",
        turnover_required=True,
        ril_control=False,
    ),
    "ES": ConditionProfile(
        condition="ES",
        text_culture="persistent",
        artifact_culture="executable",
        social_channel="issues_pr_messages",
        turnover_required=True,
        ril_control=False,
    ),
    "RIL": ConditionProfile(
        condition="RIL",
        text_culture="none",
        artifact_culture="none",
        social_channel="isolated",
        turnover_required=False,
        ril_control=True,
    ),
}


def expected_condition_profile(condition: str) -> ConditionProfile:
    try:
        return _PROFILES[condition]
    except KeyError as error:
        raise ValueError(f"Unknown experiment condition: {condition}") from error


def validate_condition_manifest(manifest: "ExperimentManifest") -> None:
    profile = expected_condition_profile(manifest.experiment.condition)
    study = manifest.study

    observed = {
        "text_culture": study.text_culture,
        "artifact_culture": study.artifact_culture,
        "social_channel": study.social_channel,
        "ril_control": study.ril_control,
    }
    expected = {
        "text_culture": profile.text_culture,
        "artifact_culture": profile.artifact_culture,
        "social_channel": profile.social_channel,
        "ril_control": profile.ril_control,
    }

    mismatches = [
        name
        for name, expected_value in expected.items()
        if observed[name] != expected_value
    ]
    if mismatches:
        raise ValueError(
            "Condition treatment mismatch for "
            f"{manifest.experiment.condition}: {', '.join(sorted(mismatches))}"
        )

    forge_expected = profile.artifact_culture == "executable"
    if manifest.runtime.forge_enabled != forge_expected:
        raise ValueError(
            "runtime.forge_enabled must match the condition artifact treatment"
        )
    if manifest.runtime.forge.enabled != forge_expected:
        raise ValueError(
            "runtime.forge.enabled must match the condition artifact treatment"
        )

    text_expected = profile.text_culture == "persistent"
    if manifest.runtime.text_culture.enabled != text_expected:
        raise ValueError(
            "runtime.text_culture.enabled must match the condition text treatment"
        )

    social_expected = profile.social_channel in {
        "direct",
        "issues_pr_messages",
    }
    if manifest.runtime.social.enabled != social_expected:
        raise ValueError(
            "runtime.social.enabled must match the condition social treatment"
        )
    expected_social_mode = (
        profile.social_channel
        if social_expected
        else "none"
    )
    if manifest.runtime.social.mode != expected_social_mode:
        raise ValueError(
            "runtime.social.mode must match the condition social treatment"
        )

    if profile.turnover_required and not manifest.agents.turnover.enabled:
        raise ValueError(
            f"Condition {profile.condition} requires controlled turnover"
        )

    if profile.turnover_required:
        expected_agents = {
            f"agent-{index + 1:04d}"
            for index in range(manifest.world.agent_count)
        }
        configured_agents = set(manifest.agents.turnover.agent_ids)
        if configured_agents != expected_agents:
            raise ValueError(
                "Controlled cultural conditions must turn over the complete population"
            )

    if study.compute_match_group is None:
        raise ValueError(
            "condition-contract manifests require study.compute_match_group"
        )

    if manifest.evaluation_profile != "v0_1-hidden-functional-suite":
        raise ValueError(
            "condition-contract manifests require the V0.1 evaluation profile"
        )

    if profile.ril_control:
        if study.ril_topology == "not_applicable":
            raise ValueError("RIL condition requires an explicit isolated topology")
    elif study.ril_topology != "not_applicable":
        raise ValueError("ril_topology is only valid for the RIL condition")


def assess_condition_support(
    manifest: "ExperimentManifest",
) -> ConditionSupportReport:
    if not manifest.study.enforce_condition_contract:
        return ConditionSupportReport(
            condition=manifest.experiment.condition,
            contract_valid=False,
            research_runtime_ready=False,
            missing_surfaces=["condition_contract_not_enabled"],
        )

    validate_condition_manifest(manifest)
    profile = expected_condition_profile(manifest.experiment.condition)

    missing: list[str] = []
    notes: list[str] = []

    if (
        profile.text_culture == "persistent"
        and not manifest.runtime.text_culture.enabled
    ):
        missing.append("persistent_text_culture_runtime")
    elif profile.text_culture == "docs_only":
        notes.append(
            "Docs-only public artifacts exist in the Forge prototype, but this "
            "does not by itself establish executable culture."
        )

    if (
        profile.social_channel == "direct"
        and (
            not manifest.runtime.social.enabled
            or manifest.runtime.social.mode != "direct"
        )
    ):
        missing.append("direct_social_channel_runtime")
    elif (
        profile.social_channel == "issues_pr_messages"
        and (
            not manifest.runtime.social.enabled
            or manifest.runtime.social.mode != "issues_pr_messages"
        )
    ):
        missing.append("issues_pr_messages_runtime")
    elif profile.social_channel == "limited":
        notes.append(
            "Personal-condition limited communication is operationalized as no "
            "direct agent-to-agent channel."
        )

    if profile.artifact_culture == "executable":
        evaluation = manifest.evaluation
        if not evaluation.enabled:
            missing.append("hidden_functional_evaluator")
        elif (
            not evaluation.sandbox_enabled
            or evaluation.sandbox_policy.backend == "none"
        ):
            missing.append("hardened_artifact_execution_runtime")
        else:
            missing.append("attested_hardened_evaluator_runtime")

    if profile.ril_control:
        notes.append(
            "RIL topology is declared but compute matching is evaluated separately; "
            "the research report permits single isolated or independent isolated learners."
        )

    return ConditionSupportReport(
        condition=profile.condition,
        research_runtime_ready=not missing,
        missing_surfaces=sorted(set(missing)),
        notes=notes,
    )



def assess_condition_runtime_readiness(
    manifest: "ExperimentManifest",
    evaluation_report: "FunctionalEvaluationReport | None",
) -> ConditionSupportReport:
    static = assess_condition_support(manifest)
    profile = expected_condition_profile(manifest.experiment.condition)
    if profile.artifact_culture != "executable":
        return static

    missing = list(static.missing_surfaces)
    notes = list(static.notes)
    evidence_gate = "attested_hardened_evaluator_runtime"

    if (
        evidence_gate in missing
        and _is_valid_hardened_evaluation_evidence(
            manifest,
            evaluation_report,
        )
    ):
        missing.remove(evidence_gate)
        notes.append(
            "A completed external-hardened hidden evaluation matches the "
            "manifest-pinned suite and sandbox policy."
        )

    return ConditionSupportReport(
        condition=static.condition,
        contract_valid=static.contract_valid,
        research_runtime_ready=not missing,
        missing_surfaces=sorted(set(missing)),
        notes=notes,
    )


def _is_valid_hardened_evaluation_evidence(
    manifest: "ExperimentManifest",
    report: "FunctionalEvaluationReport | None",
) -> bool:
    if report is None:
        return False

    evaluation = manifest.evaluation
    if (
        not evaluation.enabled
        or evaluation.suite_id is None
        or evaluation.suite_hash is None
    ):
        return False

    expected_policy_hash = state_hash(
        evaluation.sandbox_policy.model_dump(mode="json")
    )
    expected_result_hash = state_hash(
        report.model_dump(mode="json", exclude={"result_hash"})
    )

    return all(
        (
            report.experiment_id == manifest.experiment.id,
            report.condition == manifest.experiment.condition,
            report.suite_id == evaluation.suite_id,
            report.suite_hash == evaluation.suite_hash,
            report.policy_hash == expected_policy_hash,
            report.backend == evaluation.sandbox_policy.backend,
            report.runner_kind == "external-hardened",
            report.worker_build_sha256 != "0" * 64,
            report.runtime_build_sha256 != "0" * 64,
            report.total_cases <= evaluation.max_cases,
            report.result_hash == expected_result_hash,
        )
    )
