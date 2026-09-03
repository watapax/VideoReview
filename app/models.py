from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Course(SQLModel, table=True):
    """Un curso (ej: 'Animación 2D - Sección 1'). Cada curso tiene su propia
    rúbrica, su propia lista de estudiantes y sus propias tareas."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RubricAspect(SQLModel, table=True):
    """Un aspecto evaluado de la rúbrica (ej: Timing, 25%), propio de UNA tarea.

    Cada tarea tiene su propia rúbrica — dos tareas del mismo curso pueden
    evaluar aspectos distintos con ponderaciones distintas.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    assignment_id: Optional[int] = Field(default=None, foreign_key="assignment.id", index=True)
    name: str
    weight: float  # porcentaje, ej: 25.0
    order: int = 0


class Student(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: Optional[int] = Field(default=None, foreign_key="course.id", index=True)
    name: str
    active: bool = True


class Assignment(SQLModel, table=True):
    """Una tarea/entrega (ej: 'Tarea 3 - Ciclo de caminata'), propia de un curso."""

    id: Optional[int] = Field(default=None, primary_key=True)
    course_id: Optional[int] = Field(default=None, foreign_key="course.id", index=True)
    name: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Grade(SQLModel, table=True):
    """Nota + feedback de UN estudiante, en UN aspecto, para UNA tarea."""

    id: Optional[int] = Field(default=None, primary_key=True)
    assignment_id: int = Field(foreign_key="assignment.id", index=True)
    student_id: int = Field(foreign_key="student.id", index=True)
    aspect_id: int = Field(foreign_key="rubricaspect.id", index=True)
    score: Optional[float] = None  # escala 1.0 - 7.0
    feedback: str = ""
