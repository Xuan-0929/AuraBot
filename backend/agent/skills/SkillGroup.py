from .Skill import Skill



class SkillGroup:
    def __init__(self, name: str, skills: list[Skill], description: str | None = None):
        self.name = name
        self.skills = skills
        self.description = description
    
    
    def add_skill(self, skill: Skill):
        """
        添加一个技能到技能组中

        Args:
            skill (Skill): 要添加的技能对象
        """
        self.skills.append(skill)

    
    def remove_skill(self, skill_name: str):
        """
        从技能组中移除一个技能

        Args:
            skill_name (str): 要移除的技能名称
        """
        self.skills = [skill for skill in self.skills if skill.name != skill_name]
    

    def list_skills(self) -> list[Skill]:
        """
        列出技能组中的所有技能

        Returns:
            list[Skill]: 技能组中的所有技能对象列表
        """
        return self.skills