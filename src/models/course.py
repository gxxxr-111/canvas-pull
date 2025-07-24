from pydantic import BaseModel, Field


class Course(BaseModel):
    id: int = Field(alias="course_id")
    section_id: int = Field(alias="course_section_id")
    user_id: str = Field(alias="user_id")
