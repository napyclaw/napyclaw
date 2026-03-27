from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass
class Message:
    group_id: str
    channel_name: str
    sender_id: str
    sender_name: str
    text: str
    timestamp: str
    channel_type: str


class Channel(ABC):
    channel_type: str

    def __init__(self) -> None:
        self._handler: Callable[[Message], Awaitable[None]] | None = None

    def register_handler(self, handler: Callable[[Message], Awaitable[None]]) -> None:
        self._handler = handler

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def send(self, group_id: str, text: str) -> None: ...

    @abstractmethod
    async def set_typing(self, group_id: str, on: bool) -> None: ...
