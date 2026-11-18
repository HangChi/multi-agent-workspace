"""用户查询服务（T024，007）：人员选择的最小投影。

刻意不做 users CRUD：本期只需要「把需求派给谁」这一个读场景。
管理后台的用户管理若将来需要，走独立特性——现在做就是范围膨胀
（spec §2.2）。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def list_options(db: AsyncSession) -> list[User]:
    """在职用户（status=active），按邮箱排序——稳定输出便于测试与界面。

    只暴露 id/email/role（端点层投影）：角色是前端区分可见操作所需，
    不分页（公司量级，与 agents/groups 拖拽目标集合同构）。
    """
    stmt = select(User).where(User.status == "active").order_by(User.email)
    return list((await db.execute(stmt)).scalars().all())
