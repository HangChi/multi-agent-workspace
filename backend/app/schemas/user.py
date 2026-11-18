"""用户 schema（007）：人员选择的最小投影。

UserOption 刻意只有三个字段——分派选人不需要密码哈希、邀请码、
创建时间。多暴露一个字段就多一个「迟早被用上然后后悔」的面。
"""

from pydantic import BaseModel


class UserOption(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    email: str
    role: str


class UserOptionList(BaseModel):
    items: list[UserOption]
    total: int
