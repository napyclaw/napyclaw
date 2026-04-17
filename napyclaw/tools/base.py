from abc import ABC, abstractmethod


class Tool(ABC):
    name: str
    description: str
    parameters: dict
    injection_source: str = "internal"  # maps to InjectionGuard RISK_MAP

    @abstractmethod
    async def execute(self, **kwargs) -> str:
        """Execute the tool and return a string result.

        On error, returns an error string — never raises.
        The LLM receives the return value as tool result content.
        """
        ...

    @property
    def schema(self) -> dict:
        """OpenAI function-calling tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
