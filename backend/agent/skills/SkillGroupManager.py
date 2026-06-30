from .SkillGroup import SkillGroup





class SkillGroupManager:
    _instance: "SkillGroupManager | None" = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._tasks = {}
        self._initialized = True