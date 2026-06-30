from .utils import *


"""
用于存放Skill系统与SQLite交互的工具函数

从软件架构上来说，这一层是直接与SQLite交互的
"""



class SkillSQL:
    """
    这个类没有必要被实例化，只是一个工具包
    """

    @staticmethod
    def insert_skill_metadata(name: str, path: str) -> bool:
        """
        写入一条Skill的元数据

        arg:
            name: Skill名称
            path: Skill路径
        
        ret:
            True: 写入成功
            False: 写入失败
        """
        pass


    @staticmethod
    def delete_skill_metadata(name: str) -> bool:
        """
        删除一条Skill的记录

        arg:
            name: Skill名称
        
        ret:
            True: 删除成功
            False: 删除失败
        """
        pass


    @staticmethod
    def update_skill_metadata(name: str, path: str) -> bool:
        """
        对于给定Skill，更新path

        arg:
            name: 受到修改的Skill名称
            path: 新的Skill路径
        
            
        ret:
            True: 更新成功
            False: 更新失败
        """
        pass

    @staticmethod
    def get_skill_metadata(name: str) -> str:
        """
        获取一条Skill的元数据

        arg:
            name: Skill名称
        
        ret:
            str: Skill元数据
        """
        pass


    @staticmethod
    def list_all_skills() -> list[tuple[str, str]]:
        """
        展示Skill表格中所有skills

        ret:
            list[str]: 所有Skill的名称和路径
        """
        pass