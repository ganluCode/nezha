"""Event bus: register handlers and dispatch events asynchronously."""

import asyncio
import traceback
from abc import ABC, abstractmethod

from nezha.events.types import Event


class EventHandler(ABC):
    """Abstract base for event handlers."""

    @abstractmethod
    async def handle(self, event: Event) -> None:
        """Process a single event. Must not raise."""

    async def close(self) -> None:
        """Cleanup resources. Called when bus shuts down."""


class EventBus:
    """Async event bus: dispatches events to all registered handlers.

    Single handler failure does not affect other handlers.
    """

    def __init__(self):
        self._handlers: list[EventHandler] = []

    def register(self, handler: EventHandler):
        self._handlers.append(handler)

    async def emit(self, event: Event) -> None:
        """Dispatch event to all handlers. Exceptions are caught and logged."""
        for handler in self._handlers:
            try:
                await handler.handle(event)
            except Exception:
                print(f"[event_bus] Handler {type(handler).__name__} failed:")
                traceback.print_exc()

    async def close(self) -> None:
        """Close all handlers."""
        for handler in self._handlers:
            try:
                await handler.close()
            except Exception:
                pass
