from dse.contracts.event import WorldEvent
from dse.contracts.experiment import ExperimentManifest
from dse.engine.forge_actions import process_forge_actions
from dse.engine.structured_cognition import advance_structured_cognition_tick
from dse.engine.text_social import process_text_social_actions
from dse.engine.world import WorldState
from dse.forge.base import ForgeProvider
from dse.providers.base import ModelProvider


async def advance_cultural_runtime_tick(
    world: WorldState,
    manifest: ExperimentManifest,
    model_provider: ModelProvider,
    forge_provider: ForgeProvider | None = None,
) -> list[WorldEvent]:
    events = await advance_structured_cognition_tick(
        world,
        manifest,
        model_provider,
    )

    if manifest.runtime.forge_enabled and manifest.runtime.forge.enabled:
        if forge_provider is None:
            raise ValueError(
                "forge_provider is required when executable Forge culture is enabled"
            )
        events.extend(
            await process_forge_actions(
                world,
                manifest,
                forge_provider,
            )
        )

    events.extend(
        await process_text_social_actions(
            world,
            manifest,
        )
    )
    return events


async def advance_cultural_runtime_ticks(
    world: WorldState,
    manifest: ExperimentManifest,
    model_provider: ModelProvider,
    count: int,
    forge_provider: ForgeProvider | None = None,
) -> list[WorldEvent]:
    if count < 0:
        raise ValueError("count must be non-negative")

    events: list[WorldEvent] = []
    for _ in range(count):
        events.extend(
            await advance_cultural_runtime_tick(
                world,
                manifest,
                model_provider,
                forge_provider,
            )
        )
    return events
