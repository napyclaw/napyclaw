from __future__ import annotations

import json
from typing import TYPE_CHECKING

from napyclaw.models.base import ChatResponse, LLMClient, ToolCall
from napyclaw.tools.base import Tool

if TYPE_CHECKING:
    from napyclaw.config import Config
    from napyclaw.injection_guard import InjectionGuard


class AgentLoopError(Exception):
    pass


class Agent:
    def __init__(
        self,
        client: LLMClient,
        tools: list[Tool],
        system_prompt: str,
        config: Config | None = None,
        max_tool_iterations: int = 10,
        history: list[dict] | None = None,
        injection_guard: InjectionGuard | None = None,
    ) -> None:
        self.client = client
        self.tools = tools
        self.system_prompt = system_prompt
        self.config = config
        self.max_tool_iterations = max_tool_iterations
        self.history: list[dict] = history or []
        self._injection_guard = injection_guard

    @property
    def tool_schemas(self) -> list[dict]:
        return [t.schema for t in self.tools]

    def _history_budget(self) -> int:
        """Calculate token budget for conversation history."""
        # Rough heuristic: check if VectorMemory is active by looking at system prompt size
        # When VectorMemory is active, history only needs ~20 exchanges
        # Without it, history carries more weight
        has_vector = "vector" in self.system_prompt.lower() or len(self.system_prompt) > 2000
        ratio = 0.15 if has_vector else 0.30
        max_turns_cap = 20 * 300  # ~6,000 tokens

        budget = min(
            int(self.client.context_window * ratio),
            max_turns_cap,
        )

        if self.config and self.config.max_history_tokens:
            budget = self.config.max_history_tokens

        return budget

    def _prune_history(self) -> None:
        """Remove oldest exchange blocks to fit within history budget.

        An exchange block is one user message plus all subsequent assistant/tool
        messages up to the next user message. Tool call/result pairs are never split.
        """
        budget = self._history_budget()

        # Rough token estimate: 4 chars ≈ 1 token
        def estimate_tokens(msgs: list[dict]) -> int:
            return sum(len(json.dumps(m)) // 4 for m in msgs)

        while estimate_tokens(self.history) > budget and len(self.history) > 1:
            # Find the first user message boundary to remove a complete exchange
            first_user = None
            second_user = None
            for i, msg in enumerate(self.history):
                if msg.get("role") == "user":
                    if first_user is None:
                        first_user = i
                    elif second_user is None:
                        second_user = i
                        break

            if first_user is not None and second_user is not None:
                # Remove the first exchange block
                del self.history[first_user:second_user]
            else:
                # Only one exchange left, can't prune further
                break

    async def run(self, user_message: str, sender_id: str = "") -> str:
        """Run the agent with a user message. Returns the final text response."""
        if self._injection_guard:
            verdicts = await self._injection_guard.review(user_message, "user_input", self.client)
            if any(v.risk == "malicious" for v in verdicts):
                return "[Message blocked: potential injection attempt detected]"

        self.history.append({"role": "user", "content": user_message})
        self._prune_history()

        messages = [{"role": "system", "content": self.system_prompt}] + self.history

        tools_arg = self.tool_schemas if self.tools else None
        iterations = 0

        while True:
            response = await self.client.chat(messages, tools=tools_arg)

            if response.tool_calls:
                iterations += 1
                if iterations > self.max_tool_iterations:
                    raise AgentLoopError(
                        f"Agent exceeded {self.max_tool_iterations} tool iterations"
                    )

                # Append assistant message with tool calls
                assistant_msg = {
                    "role": "assistant",
                    "content": response.text,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in response.tool_calls
                    ],
                }
                self.history.append(assistant_msg)
                messages.append(assistant_msg)

                # Execute each tool call
                for tc in response.tool_calls:
                    tool = self._find_tool(tc.name)
                    if tool is None:
                        result = f"Error: unknown tool '{tc.name}'"
                    else:
                        result = await tool.execute(sender_id=sender_id, **tc.arguments)
                        if self._injection_guard:
                            source = getattr(tool, "injection_source", "unknown")
                            verdicts = await self._injection_guard.review(result, source, self.client)
                            if any(v.risk == "malicious" for v in verdicts):
                                result = f"[Tool result blocked: injection attempt detected from {tc.name}]"
                            elif any(v.risk == "suspicious" for v in verdicts):
                                result = f"[Warning: suspicious content in result]\n{result}"

                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                    self.history.append(tool_msg)
                    messages.append(tool_msg)
            else:
                # Final text response — use streaming internally but buffer
                final_text = response.text or ""

                self.history.append({"role": "assistant", "content": final_text})
                return final_text

    def _find_tool(self, name: str) -> Tool | None:
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None
