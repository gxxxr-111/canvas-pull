"""Canvas API objects (only the fields canvas-pull uses)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Term(_Model):
    id: int | None = None
    name: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None


class Course(_Model):
    id: int
    name: str | None = None
    course_code: str | None = None
    term: Term | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    # Courses outside their access dates come back with only id + this flag.
    access_restricted_by_date: bool = False
    # Set by canvas-pull (not Canvas) for courses from completed enrollments.
    completed: bool = False

    @property
    def label(self) -> str:
        return self.name or self.course_code or f"Course {self.id}"


class Folder(_Model):
    id: int
    full_name: str
    name: str = ""
    parent_folder_id: int | None = None


class CanvasFile(_Model):
    id: int
    display_name: str
    filename: str = ""
    folder_id: int | None = None
    size: int = 0
    content_type: str = Field(default="", alias="content-type")
    url: str = ""
    updated_at: datetime | None = None
    modified_at: datetime | None = None
    locked_for_user: bool = False

    @property
    def stamp(self) -> str | None:
        """Timestamp of the last content change (modified_at ignores renames)."""
        ts = self.modified_at or self.updated_at
        return ts.isoformat() if ts else None
