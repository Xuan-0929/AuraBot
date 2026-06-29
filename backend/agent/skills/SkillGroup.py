from .Skill import Skill



class SkillGroup:
    def __init__(self, name: str, skills: list[Skill]):
        self.name = name
        self.skills = skills