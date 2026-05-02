from __future__ import annotations

from unittest.mock import MagicMock

from napyclaw.prompt_builder import PromptBuilder, RetrievedMemory, SpecialistMemoryRow


def _make_ctx(
    display_name: str = "Amy",
    job_title: str | None = "Financial Analyst",
    job_description: str | None = "I help with forecasting and P&L analysis.",
    is_first_interaction: bool = False,
) -> object:
    ctx = MagicMock()
    ctx.display_name = display_name
    ctx.job_title = job_title
    ctx.job_description = job_description
    ctx.is_first_interaction = is_first_interaction
    ctx.active_client.provider = "openai"
    ctx.active_client.model = "gpt-4o"
    return ctx


def _make_memory(
    responsibilities: list[str] | None = None,
    working_context: list[str] | None = None,
    episodic: list[str] | None = None,
) -> RetrievedMemory:
    return RetrievedMemory(
        responsibilities=[
            SpecialistMemoryRow(id="r1", type="responsibility", content=c)
            for c in (responsibilities or [])
        ],
        working_context=[
            SpecialistMemoryRow(id="w1", type="task", content=c)
            for c in (working_context or [])
        ],
        episodic=episodic or [],
    )


class TestPromptBuilderMarkdown:
    def test_identity_block_contains_name(self):
        builder = PromptBuilder()
        memory = _make_memory()
        result = builder.build(_make_ctx(), memory, owner_name="Nate")
        assert "Amy" in result

    def test_identity_block_contains_owner(self):
        builder = PromptBuilder()
        memory = _make_memory()
        result = builder.build(_make_ctx(), memory, owner_name="Nate")
        assert "Nate" in result

    def test_identity_block_contains_job_description(self):
        builder = PromptBuilder()
        memory = _make_memory()
        result = builder.build(_make_ctx(), memory, owner_name="Nate")
        assert "forecasting and P&L" in result

    def test_onboarding_prompt_when_no_job_description(self):
        builder = PromptBuilder()
        ctx = _make_ctx(job_description=None, is_first_interaction=True)
        memory = _make_memory()
        result = builder.build(ctx, memory, owner_name="Nate")
        assert "collaboratively" in result.lower() or "define" in result.lower()

    def test_responsibilities_block_present(self):
        builder = PromptBuilder()
        memory = _make_memory(responsibilities=["I own the monthly P&L report."])
        result = builder.build(_make_ctx(), memory, owner_name="Nate")
        assert "I own the monthly P&L report." in result

    def test_working_context_block_present(self):
        builder = PromptBuilder()
        memory = _make_memory(working_context=["Prepare the Q2 forecast by Friday."])
        result = builder.build(_make_ctx(), memory, owner_name="Nate")
        assert "Q2 forecast" in result

    def test_episodic_block_present(self):
        builder = PromptBuilder()
        memory = _make_memory(episodic=["User prefers bullet-point summaries."])
        result = builder.build(_make_ctx(), memory, owner_name="Nate")
        assert "bullet-point" in result

    def test_first_person_instruction_in_identity(self):
        builder = PromptBuilder()
        memory = _make_memory()
        result = builder.build(_make_ctx(), memory, owner_name="Nate")
        assert "first person" in result.lower() or 'speak as "I"' in result.lower() or "I," in result

    def test_identity_before_responsibilities(self):
        builder = PromptBuilder()
        memory = _make_memory(responsibilities=["Own P&L."])
        result = builder.build(_make_ctx(), memory, owner_name="Nate")
        assert result.index("Amy") < result.index("Own P&L.")

    def test_responsibilities_before_working_context(self):
        builder = PromptBuilder()
        memory = _make_memory(
            responsibilities=["RESP_MARKER"],
            working_context=["CTX_MARKER"],
        )
        result = builder.build(_make_ctx(), memory, owner_name="Nate")
        assert result.index("RESP_MARKER") < result.index("CTX_MARKER")


class TestPromptBuilderJson:
    def test_json_format_contains_identity_key(self):
        builder = PromptBuilder()
        memory = _make_memory()
        result = builder.build(_make_ctx(), memory, owner_name="Nate", fmt="json")
        import json
        parsed = json.loads(result)
        assert "identity" in parsed

    def test_json_format_contains_responsibilities_key(self):
        builder = PromptBuilder()
        memory = _make_memory(responsibilities=["Own P&L."])
        result = builder.build(_make_ctx(), memory, owner_name="Nate", fmt="json")
        import json
        parsed = json.loads(result)
        assert "responsibilities" in parsed
        assert "Own P&L." in parsed["responsibilities"]
