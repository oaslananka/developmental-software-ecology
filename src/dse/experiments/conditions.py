from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from dse.contracts.experiment import ExperimentManifest


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

    if profile.text_culture == "persistent":
        missing.append("persistent_text_culture_runtime")
    elif profile.text_culture == "docs_only":
        notes.append(
            "Docs-only public artifacts exist in the Forge prototype, but this "
            "does not by itself establish executable culture."
        )

    if profile.social_channel == "direct":
        missing.append("direct_social_channel_runtime")
    elif profile.social_channel == "issues_pr_messages":
        missing.append("issues_pr_messages_runtime")
    elif profile.social_channel == "limited":
        notes.append(
            "M14 operationalizes Personal-condition limited communication as no "
            "direct agent-to-agent channel."
        )

    if profile.artifact_culture == "executable":
        if not manifest.runtime.sandbox_enabled:
            missing.append("hardened_artifact_execution_runtime")
        missing.append("hidden_functional_evaluator")

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
