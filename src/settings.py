from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    url: str = Field(
        alias="website",
        default="oc.sjtu.edu.cn",
    )

    token: str = Field(
        alias="access_token",
        min_length=1,
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
