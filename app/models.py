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


class Video(SQLModel, table=True):
    """Un video (siempre .mp4) subido para UN estudiante en UNA tarea.

    Pueden existir varios videos para el mismo par tarea+estudiante (ej.
    distintos intentos). El archivo en sí vive en Cloudflare R2 — acá solo
    se guarda la referencia (object_key) y algunos metadatos para mostrar
    en la lista, no el archivo.

    Es una tabla nueva (no una migración de columnas de una tabla vieja),
    así que basta con que `init_db()` la cree vía metadata.create_all(); no
    necesita su propia función de migración como course_id/assignment_id.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    assignment_id: int = Field(foreign_key="assignment.id", index=True)
    student_id: int = Field(foreign_key="student.id", index=True)
    label: str = ""
    object_key: str
    original_filename: str = ""
    size_bytes: int = 0
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)
    # Token largo y aleatorio para el link público de solo lectura
    # (/watch/{share_token}) que le permite al estudiante ver sus anotaciones
    # sin entrar al panel de notas. No es una migración de columna nueva sin
    # más: como Video ya existía en instalaciones previas, _migrate_add_
    # video_share_token() en database.py le agrega esta columna y le genera
    # un token a los videos que ya estaban subidos.
    share_token: str = Field(default="", index=True)


class Annotation(SQLModel, table=True):
    """Una anotación dibujada a mano sobre UN video, en un momento específico
    (en segundos, no en número de frame — ver plan-revision-video.md).

    `drawing_data` guarda el dibujo como datos vectoriales (JSON: una lista
    de trazos, cada uno una lista de puntos [x, y] normalizados entre 0 y 1
    respecto al tamaño del video) en vez de una imagen — así se ve nítido
    sin importar el tamaño de pantalla donde se reproduzca.

    Igual que Video, es una tabla nueva: no necesita su propia función de
    migración, basta con que init_db() la cree vía metadata.create_all().
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: int = Field(foreign_key="video.id", index=True)
    time_seconds: float = 0.0
    color: str = "#5b7cfa"
    stroke_width: float = 4.0
    drawing_data: str = "{}"
    note: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
