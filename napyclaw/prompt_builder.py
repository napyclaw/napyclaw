from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    pass


@dataclass
class SpecialistMemoryRow:
    id: str
    type: str
    content: str


@dataclass
class RetrievedMemory:
    responsibilities: list[SpecialistMemoryRow] = field(default_factory=list)
    working_context: list[SpecialistMemoryRow] = field(default_factory=list)
    episodic: list[str] = field(default_factory=list)


_TRUST_TIER_RULES = (
    "Memory update rules:\n"
    "- For responsibility or job_description changes: propose first, wait for user confirmation before saving.\n"
    "- For task, tool, resource, preference, fact: save immediately and notify the user in the Backstage panel.\n"
    "- If a request is outside your current responsibilities, ask: "
    "'This seems outside my current scope — should I add this as a new responsibility?'"
)

_FIRST_PERSON_RULE = (
    "Always speak in first person. You are a named specialist talking directly to the user. "
    'Use "I", "my role", "I can help with..." — never refer to yourself in the third person.'
)

_ONBOARDING_INSTRUCTION = (
    "Your job description has not been defined yet. "
    "Before doing other work, collaboratively define your role with the user. "
    "Ask open questions about what they need from you. "
    "After a few turns, propose a summary: 'Here is what I understand my role to be — does this look right?' "
    "Once confirmed, use set_job_description to save it, then seed initial specialist_memory entries. "
    "Announce when you are ready to work. "
    "Then ask: 'Are there any specific resources or knowledge I will need to do this role at the highest level?'"
)


class PromptBuilder:
    def build(
        self,
        ctx: object,
        memory: RetrievedMemory,
        owner_name: str,
        fmt: Literal["markdown", "json"] = "markdown",
    ) -> str:
        blocks = self._build_blocks(ctx, memory, owner_name)
        if fmt == "json":
            return self._render_json(blocks)
        return self._render_markdown(blocks)

    def _build_blocks(self, ctx: object, memory: RetrievedMemory, owner_name: str) -> dict[str, str]:
        job_description = getattr(ctx, "job_description", None)
        display_name = getattr(ctx, "display_name", "Specialist")
        job_title = getattr(ctx, "job_title", None)

        identity_parts = [
            f"Your name is {display_name}.",
        ]
        if job_title:
            identity_parts.append(f"Your role is: {job_title}.")
        if job_description:
            identity_parts.append(f"Your job description: {job_description}")
        else:
            identity_parts.append(_ONBOARDING_INSTRUCTION)
        identity_parts.append(f"You are working for {owner_name}.")
        identity_parts.append(_FIRST_PERSON_RULE)
        identity_parts.append(_TRUST_TIER_RULES)

        blocks: dict[str, str] = {
            "identity": "\n".join(identity_parts),
        }

        if memory.responsibilities:
            blocks["responsibilities"] = "\n".join(
                f"- {r.content}" for r in memory.responsibilities
            )

        if memory.working_context:
            blocks["working_context"] = "\n".join(
                f"- [{r.type}] {r.content}" for r in memory.working_context
            )

        if memory.episodic:
            blocks["episodic_memory"] = "\n".join(
                f"- {e}" for e in memory.episodic
            )

        return blocks

    def _render_markdown(self, blocks: dict[str, str]) -> str:
        section_titles = {
            "identity": "## Identity",
            "responsibilities": "## My Responsibilities",
            "working_context": "## Working Context",
            "episodic_memory": "## Relevant Memory",
        }
        parts = []
        for key, content in blocks.items():
            title = section_titles.get(key, f"## {key.replace('_', ' ').title()}")
            parts.append(f"{title}\n{content}")
        return "\n\n".join(parts)

    def _render_json(self, blocks: dict[str, str]) -> str:
        return json.dumps(blocks, ensure_ascii=False, indent=2)
