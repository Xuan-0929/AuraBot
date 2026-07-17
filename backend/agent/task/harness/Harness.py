from abc import ABC, abstractmethod

from ...LLMClient import LLMClient
from ..Task import Task


class Harness(ABC):
    """One task-local agent loop backed by a shared LLM client."""

    async def __init__(self, *, task: Task, llm_client: LLMClient):
        self._task = task
        self._llm_client = llm_client

        self.task.messages.append(
            {
                "role": "system",
                "content": await self._create_system_prompt()
            }
        )
        self.task.messages.append(
            {
                "role": "user",
                "content": await self._create_user_prompt()
            }
        )

    @property
    async def task(self) -> Task:
        return self._task

    @property
    async def llm_client(self) -> LLMClient:
        return self._llm_client

    @abstractmethod
    async def run(self) -> object:
        """Run this harness's agent loop and return its eventual result."""
        raise NotImplementedError


    async def _create_system_prompt(self) -> str:
        """Create a system prompt for the agent loop."""
        raise NotImplementedError
    
    async def _create_user_prompt(self) -> str:
        """Create a user prompt for the agent loop."""
        return self.task.task_prompt