import utils


"""
用于存放Skill系统与SQLite交互的工具函数
"""



class SkillSQL:
    """
    这个类没有必要被实例化，只是一个工具包
    """

    @staticmethod
    def get_skill(name: str) -> str:
        return utils.get_skill(name)