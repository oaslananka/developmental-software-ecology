import re

from dse.contracts.agent import AgentState, EpisodicMemory


_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")


def retrieve_episodes(
    agent: AgentState,
    *,
    query_text: str,
    current_tick: int,
    limit: int,
) -> list[EpisodicMemory]:
    if limit <= 0:
        return []

    query_tokens = _tokens(query_text)
    scored: list[tuple[float, EpisodicMemory]] = []

    for episode in agent.memory.episodes:
        content_tokens = _tokens(
            " ".join(
                part
                for part in (
                    episode.content,
                    episode.decision or "",
                    episode.focus or "",
                )
                if part
            )
        )
        overlap = _overlap_score(query_tokens, content_tokens)
        age = max(0, current_tick - episode.created_tick)
        recency = 1.0 / (1.0 + age)
        score = (2.0 * overlap) + episode.salience + (0.25 * recency)
        scored.append((score, episode))

    scored.sort(
        key=lambda item: (
            -item[0],
            -item[1].created_tick,
            item[1].memory_id,
        )
    )
    return [episode for _, episode in scored[:limit]]


def select_eviction_candidate(agent: AgentState) -> EpisodicMemory:
    if not agent.memory.episodes:
        raise ValueError("Cannot evict from empty memory")

    return min(
        agent.memory.episodes,
        key=lambda episode: (
            episode.salience,
            episode.created_tick,
            episode.memory_id,
        ),
    )


def memory_context(episode: EpisodicMemory) -> dict[str, object]:
    return {
        "memory_id": episode.memory_id,
        "created_tick": episode.created_tick,
        "content": episode.content,
        "salience": episode.salience,
        "decision": episode.decision,
        "focus": episode.focus,
    }


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text)}


def _overlap_score(query_tokens: set[str], content_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    return len(query_tokens & content_tokens) / len(query_tokens)
