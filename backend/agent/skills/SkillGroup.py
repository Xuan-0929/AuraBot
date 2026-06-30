from .Skill import Skill



class SkillGroup:
    def __init__(self, name: str, skills: list[Skill], description: str | None = None):
        self.name = name
        self.skills = skills
        self.description = description
    
    