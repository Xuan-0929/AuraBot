from collections.abc import Mapping

from ...LLMClient import LLMClient
from .Harness import Harness
from .PlanAndSolveHarness import PlanAndSolveHarness
from .ReActHarness import ReActHarness
from ..Task import Task, TaskType

HarnessType = type[Harness]


class HarnessFactory:
    """Create isolated harnesses that share one process-scoped LLM client."""

    DEFAULT_HARNESS_TYPES: Mapping[TaskType, HarnessType] = {
        TaskType.ReAct: ReActHarness,
        TaskType.PlanAndSolve: PlanAndSolveHarness,
    }

    def __init__(
        self,
        *,
        llm_client: LLMClient,
        harness_types: Mapping[TaskType, HarnessType] | None = None,
    ):
        self._llm_client = llm_client
        self._harness_types = dict(
            self.DEFAULT_HARNESS_TYPES if harness_types is None else harness_types
        )

        for task_type, harness_type in self._harness_types.items():
            self._validate_registration(task_type, harness_type)

    @property
    def llm_client(self) -> LLMClient:
        return self._llm_client

    def create(self, task: Task) -> Harness:
        if not isinstance(task, Task):
            raise TypeError("task must be a Task")

        if not isinstance(task.task_type, TaskType):
            raise TypeError("task.task_type must be a TaskType")

        try:
            harness_type = self._harness_types[task.task_type]
        except KeyError as exc:
            raise ValueError(
                f"No harness registered for task type: {task.task_type.value}"
            ) from exc

        return harness_type(
            task=task,
            llm_client=self._llm_client,
        )

    def register(
        self,
        task_type: TaskType,
        harness_type: HarnessType,
        *,
        replace: bool = False,
    ) -> None:
        self._validate_registration(task_type, harness_type)

        if task_type in self._harness_types and not replace:
            raise ValueError(
                f"Harness already registered for task type: {task_type.value}"
            )

        self._harness_types[task_type] = harness_type

    def registered_task_types(self) -> tuple[TaskType, ...]:
        return tuple(self._harness_types)

    @staticmethod
    def _validate_registration(
        task_type: TaskType,
        harness_type: HarnessType,
    ) -> None:
        if not isinstance(task_type, TaskType):
            raise TypeError("task_type must be a TaskType")

        if not isinstance(harness_type, type) or not issubclass(harness_type, Harness):
            raise TypeError("harness_type must inherit from Harness")
