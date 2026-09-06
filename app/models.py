from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Teacher(SQLModel, table=True):
    """Una cuenta de docente. Cada uno se registra solo (ver /signup en
    main.py, requiere el código de invitación de la app) y desde ahí crea y
    administra sus propios cursos (ver Course.owner_teacher_id).

    `name` es el nombre con el que inicia sesión (no un email) — se guarda
    también en minúscula en `name_lower` para poder buscarlo sin
    distinguir mayúsculas/minúsculas sin depender de collation de SQLite.
    `password_hash` guarda sal+hash juntos como un solo string (ver
    app.auth.hash_password/verify_password) — no se guarda la contraseña
    en texto plano en ningún momento.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    name_lower: str = Field(index=True, unique=True)
    password_hash: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Course(SQLModel, table=True):
    """Un curso (ej: 'Animación 2D - Sección 1'). Cada curso tiene su propia
    rúbrica, su propia lista de estudiantes y sus propias tareas.

    `owner_teacher_id` es el docente dueño (quien lo creó — cada /signup
    nuevo crea automáticamente un curso propio en blanco para el docente
    recién registrado, ver main.py): todos los docentes VEN todos los
    cursos activos, pero solo el dueño puede editarlos (renombrarlo,
    agregar tareas/estudiantes, poner notas, subir videos, anotar). None
    solo puede darse en cursos de una instalación previa a que existieran
    las cuentas — ver _migrate_add_course_owner() en database.py; esos
    cursos huérfanos no se le asignan a nadie automáticamente, así que
    cualquier docente logueado puede editarlos hasta que alguien los
    reclame a mano.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    active: bool = True
    owner_teacher_id: Optional[int] = Field(default=None, foreign_key="teacher.id", index=True)
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

    `rubric_aspect_id` es opcional: un video puede (o no) ser la evidencia de
    UN aspecto puntual de la rúbrica de esa tarea (ej: el video que muestra
    el "Timing" de un estudiante) — se vincula a mano desde la pestaña
    Videos (ver grading.html / video_upload en main.py), y cuando está
    vinculado, el informe (report.html) lo muestra junto al feedback de ese
    aspecto para ese estudiante. Ver _migrate_add_video_aspect() en
    database.py para instalaciones existentes.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    assignment_id: int = Field(foreign_key="assignment.id", index=True)
    student_id: int = Field(foreign_key="student.id", index=True)
    label: str = ""
    object_key: str
    original_filename: str = ""
    size_bytes: int = 0
    rubric_aspect_id: Optional[int] = Field(default=None, foreign_key="rubricaspect.id", index=True)
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)


class VideoShare(SQLModel, table=True):
    """Token del link público de solo lectura (/watch/{share_token}).

    Es por TAREA + ESTUDIANTE, no por video individual — una tarea puede
    tener más de un video (varios intentos) por estudiante, y el profesor
    quiere un solo link que los cubra todos, con navegación simple entre
    ellos (ver /watch/{share_token} en main.py). Se crea la primera vez que
    el profesor pide el link (botón "Compartir"), no al subir el video —
    así no queda un token por cada intento, sino uno solo por estudiante y
    tarea. Es una tabla nueva: no necesita su propia función de migración,
    basta con que init_db() la cree vía metadata.create_all().
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    assignment_id: int = Field(foreign_key="assignment.id", index=True)
    student_id: int = Field(foreign_key="student.id", index=True)
    share_token: str = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ReportShare(SQLModel, table=True):
    """Token del link público de solo lectura del informe de curso
    (/report/{share_token}) — es por TAREA (todo el curso a la vez), no por
    estudiante: el informe ya reúne a propósito el feedback de todo el curso
    agrupado por aspecto, para que se aprenda del feedback de los
    compañeros (ver la intro de report.html). Se crea la primera vez que el
    profesor pide el link (botón "Compartir informe" en /assignments/{id}/report).
    Es una tabla nueva: no necesita su propia función de migración, basta
    con que init_db() la cree vía metadata.create_all().
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    assignment_id: int = Field(foreign_key="assignment.id", index=True)
    share_token: str = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Annotation(SQLModel, table=True):
    """Una anotación dibujada a mano sobre UN video, en un momento específico
    (en segundos, no en número de frame — ver plan-revision-video.md).

    `drawing_data` guarda el dibujo como datos vectoriales (JSON: una lista
    de trazos, cada uno una lista de puntos [x, y] normalizados entre 0 y 1
    respecto al tamaño del video) en vez de una imagen — así se ve nítido
    sin importar el tamaño de pantalla donde se reproduzca.

    `end_time_seconds` es opcional: None significa una anotación "puntual"
    (un solo instante, comportamiento original). Cuando tiene un valor,
    la anotación dura desde time_seconds hasta end_time_seconds, y se
    muestra como una barra (no un rombo) en la línea de tiempo, con
    manejadores para estirarla — ver _migrate_add_annotation_range() en
    database.py, que agrega esta columna a instalaciones ya existentes.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: int = Field(foreign_key="video.id", index=True)
    time_seconds: float = 0.0
    end_time_seconds: Optional[float] = None
    color: str = "#5b7cfa"
    stroke_width: float = 4.0
    drawing_data: str = "{}"
    note: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
