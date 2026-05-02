"""Actor runtime dispatch for agentic workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from workflows.config import ActorConfig, AgenticConfig


DEFAULT_ACTOR_OUTPUT = (
    '{"status":"done","summary":"local actor completed",'
    '"artifacts":[],"validation":[],"next_recommendation":"complete"}'
)


class ActorRuntime(Protocol):
    def run(self, *, actor: ActorConfig, prompt: str) -> str: ...


@dataclass(frozen=True)
class LocalRuntime:
    output: str

    def run(self, *, actor: ActorConfig, prompt: str) -> str:
        return self.output


def build_actor_runtime(*, config: AgenticConfig, actor: ActorConfig) -> ActorRuntime:
    runtime = config.runtimes[actor.runtime]
    if runtime.kind != "local":
        raise RuntimeError(
            f"agentic first slice supports only local runtime mechanics; "
            f"actor {actor.name} uses {runtime.kind}"
        )
    output = (
        actor.raw.get("output") or runtime.raw.get("output") or DEFAULT_ACTOR_OUTPUT
    )
    return LocalRuntime(output=str(output))
