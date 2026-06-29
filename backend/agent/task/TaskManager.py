from .Task import Task



class TaskManager:
    _instance: "TaskManager | None" = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._tasks = {}
        self._initialized = True

    def create_task(self, task_id: str, task_type: str, task_data: dict):
        self._tasks[task_id] = {
            "id": task_id,
            "type": task_type,
            "data": task_data,
        }

    def list_tasks(self):
        return list(self._tasks.values())
