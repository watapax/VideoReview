import os
import secrets
import sqlite3

from sqlmodel import SQLModel, Session, create_engine

DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "correccion.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

# check_same_thread=False: uvicorn puede usar varios threads; para el volumen de uso
# de esta app (un solo profesor) SQLite es más que suficiente.
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def _table_exists(cursor: sqlite3.Cursor, table: str) -> bool:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None


def _column_exists(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def _migrate_add_courses() -> None:
    """Migración de una instalación previa (sin cursos) a la que sí los tiene.

    Si el archivo de base de datos ya tenía estudiantes/tareas de una versión
    anterior de la app (sin el concepto de "curso"), les agrega la columna
    course_id y los deja todos agrupados en un curso nuevo llamado "Curso 1",
    para no perder nada de lo que el profesor ya había cargado. Es seguro
    correrla en cada arranque: si ya está migrada, no hace nada.

    rubricaspect no se incluye aquí: esa tabla ya no tiene course_id en el
    modelo actual (la rúbrica es de la tarea, no del curso) — su propia
    migración de "course_id" a "assignment_id" vive en
    _migrate_rubric_to_assignments().
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        tables = [t for t in ("student", "assignment") if _table_exists(cur, t)]
        pending = [t for t in tables if not _column_exists(cur, t, "course_id")]
        if not pending:
            return

        cur.execute("SELECT id FROM course ORDER BY id LIMIT 1")
        row = cur.fetchone()
        if row:
            default_course_id = row[0]
        else:
            cur.execute("INSERT INTO course (name, active, created_at) VALUES (?, 1, datetime('now'))", ("Curso 1",))
            default_course_id = cur.lastrowid

        for t in pending:
            cur.execute(f"ALTER TABLE {t} ADD COLUMN course_id INTEGER")
            cur.execute(f"UPDATE {t} SET course_id = ? WHERE course_id IS NULL", (default_course_id,))

        conn.commit()
    finally:
        conn.close()


def _migrate_rubric_to_assignments() -> None:
    """Migración: la rúbrica pasa de ser del CURSO a ser de cada TAREA.

    Antes, los aspectos (rubricaspect) tenían course_id y se compartían entre
    todas las tareas de un curso. Ahora cada tarea tiene su propia copia de
    aspectos, para poder tener ponderaciones distintas por tarea. Esta
    migración: por cada tarea existente, copia los aspectos que tenía su
    curso en aspectos propios de esa tarea, y actualiza las notas (grade) ya
    guardadas para que apunten a esa copia — sin perder ninguna nota ni
    feedback ya escrito. Segura de correr en cada arranque (no hace nada si
    ya está migrada).
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        if not _table_exists(cur, "rubricaspect") or not _table_exists(cur, "assignment"):
            return
        if not _column_exists(cur, "rubricaspect", "assignment_id"):
            cur.execute("ALTER TABLE rubricaspect ADD COLUMN assignment_id INTEGER")

        has_course_id = _column_exists(cur, "rubricaspect", "course_id")
        if not has_course_id:
            # Instalación nueva (ya nace con assignment_id) o ya migrada antes.
            conn.commit()
            return

        cur.execute("SELECT id, course_id FROM assignment")
        assignments = cur.fetchall()

        for assignment_id, course_id in assignments:
            if course_id is None:
                continue
            cur.execute(
                "SELECT id, name, weight, \"order\" FROM rubricaspect WHERE course_id = ? AND assignment_id IS NULL",
                (course_id,),
            )
            course_aspects = cur.fetchall()
            for old_id, name, weight, order in course_aspects:
                cur.execute(
                    "INSERT INTO rubricaspect (assignment_id, name, weight, \"order\") VALUES (?, ?, ?, ?)",
                    (assignment_id, name, weight, order),
                )
                new_id = cur.lastrowid
                cur.execute(
                    "UPDATE grade SET aspect_id = ? WHERE assignment_id = ? AND aspect_id = ?",
                    (new_id, assignment_id, old_id),
                )

        # Los aspectos de curso ya copiados (o huérfanos, sin ninguna tarea) sobran.
        cur.execute("DELETE FROM rubricaspect WHERE assignment_id IS NULL")
        conn.commit()
    finally:
        conn.close()


def _migrate_add_video_share_token() -> None:
    """Migración: agrega la columna share_token a video (tabla que ya existía
    de la fase 1, antes de que hubiera link público) y le genera un token
    aleatorio a cada video que todavía no tenga uno — así los videos subidos
    antes de esta función también quedan con un link para compartir, sin que
    el profesor tenga que volver a subirlos. Segura de correr en cada
    arranque: si ya están todos migrados, no hace nada.
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        if not _table_exists(cur, "video"):
            return
        if not _column_exists(cur, "video", "share_token"):
            cur.execute("ALTER TABLE video ADD COLUMN share_token TEXT")

        cur.execute("SELECT id FROM video WHERE share_token IS NULL OR share_token = ''")
        pending_ids = [row[0] for row in cur.fetchall()]
        for video_id in pending_ids:
            cur.execute(
                "UPDATE video SET share_token = ? WHERE id = ?",
                (secrets.token_urlsafe(16), video_id),
            )

        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _migrate_add_courses()
    _migrate_rubric_to_assignments()
    _migrate_add_video_share_token()


def get_session():
    with Session(engine) as session:
        yield session
