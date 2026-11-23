"""Composable termination conditions for orchestrators or outer loops."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.messages import Message
from ..core.types import StopMessage


class BaseTermination(ABC):
    def __init__(self) -> None:
        self._met = False
        self._reason = ""

    @abstractmethod
    def check(self, new_messages: list[Message]) -> StopMessage | None:
        """Return StopMessage if termination is met."""

    def is_met(self) -> bool:
        return self._met

    def reset(self) -> None:
        self._met = False
        self._reason = ""

    def _stop(self, reason: str) -> StopMessage:
        self._met = True
        self._reason = reason
        return StopMessage(content=reason, source=self.__class__.__name__)

    def __or__(self, other: "BaseTermination") -> "CompositeTermination":
        return CompositeTermination([self, other], mode="any")

    def __and__(self, other: "BaseTermination") -> "CompositeTermination":
        return CompositeTermination([self, other], mode="all")


class MaxMessageTermination(BaseTermination):
    def __init__(self, max_messages: int):
        super().__init__()
        self.max_messages = max_messages
        self.count = 0

    def check(self, new_messages: list[Message]) -> StopMessage | None:
        self.count += len(new_messages)
        if self.count >= self.max_messages:
            return self._stop(f"Maximum messages reached ({self.count}/{self.max_messages})")
        return None

    def reset(self) -> None:
        super().reset()
        self.count = 0


class TextMentionTermination(BaseTermination):
    def __init__(self, text: str, case_sensitive: bool = False):
        super().__init__()
        self.text = text
        self.case_sensitive = case_sensitive

    def check(self, new_messages: list[Message]) -> StopMessage | None:
        needle = self.text if self.case_sensitive else self.text.lower()
        for message in new_messages:
            content = message.content if self.case_sensitive else message.content.lower()
            if needle in content:
                return self._stop(f"Text mention found: {self.text}")
        return None


class CompositeTermination(BaseTermination):
    def __init__(self, conditions: list[BaseTermination], mode: str = "any"):
        super().__init__()
        if mode not in ("any", "all"):
            raise ValueError("mode must be 'any' or 'all'")
        self.conditions = conditions
        self.mode = mode

    def check(self, new_messages: list[Message]) -> StopMessage | None:
        results = [condition.check(new_messages) for condition in self.conditions]
        met = any(results) if self.mode == "any" else all(results)
        if met:
            reason = "; ".join(result.content for result in results if result)
            return self._stop(reason)
        return None

    def reset(self) -> None:
        super().reset()
        for condition in self.conditions:
            condition.reset()

