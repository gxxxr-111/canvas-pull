from pydantic import BaseModel

from .course import Course


class Enrollments(BaseModel):
    courses: list[Course]

    class Config:
        min_items = 1
