from pydantic import BaseModel, Field


class User(BaseModel):
    id: int = Field()
    sid: int = Field(alias="login_id")
