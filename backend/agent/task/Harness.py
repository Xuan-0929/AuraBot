from abc import ABC, abstractmethod

from ..LLMClient import LLMClient
from .Task import Task


class Harness(ABC):
    """One task-local agent loop backed by a shared LLM client."""

    def __init__(self, *, task: Task, llm_client: LLMClient):
        self._task = task
        self._llm_client = llm_client

    @property
    def task(self) -> Task:
        return self._task

    @property
    def llm_client(self) -> LLMClient:
        return self._llm_client

    @abstractmethod
    async def run(self) -> object:
        """Run this harness's agent loop and return its eventual result."""
        raise NotImplementedError
