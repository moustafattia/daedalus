"""Agentic workflow policy section parsing."""
from __future__ import annotations

from dataclasses import dataclass
import re


class AgenticPolicyError(RuntimeError):
    """Raised when the Markdown policy chunks are missing or malformed."""


@dataclass(frozen=True)
class ActorPolicy:
    name: str
    body: str


@dataclass(frozen=True)
class AgenticPolicy:
    orchestrator: str
    actors: dict[str, ActorPolicy]


_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def parse_agentic_policy(markdown_body: str) -> AgenticPolicy:
    sections: list[tuple[str, str]] = []
    body = markdown_body or ""
    matches = list(_HEADING_RE.finditer(body))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections.append((match.group(1).strip(), body[start:end].strip()))

    orchestrator = ""
    actors: dict[str, ActorPolicy] = {}
    for title, section_body in sections:
        if title == "Orchestrator Policy":
            orchestrator = section_body
            continue
        if title.startswith("Actor:"):
            name = title.split(":", 1)[1].strip()
            if not name:
                raise AgenticPolicyError("actor policy heading is missing a name")
            actors[name] = ActorPolicy(name=name, body=section_body)

    if not orchestrator:
        raise AgenticPolicyError("missing # Orchestrator Policy section")
    if not actors:
        raise AgenticPolicyError("missing # Actor: <name> policy sections")
    return AgenticPolicy(orchestrator=orchestrator, actors=actors)
