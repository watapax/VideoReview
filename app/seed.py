"""Datos por defecto para partir: la rúbrica de ejemplo que se mostró en las maquetas.

El profesor puede editar o reemplazar estos aspectos completos desde la
rúbrica de cada tarea una vez que la app esté corriendo — esto es solo para
no partir con la pantalla vacía.
"""

from sqlmodel import Session, select

from app.models import Assignment, Course, RubricAspect

DEFAULT_ASPECTS = [
    ("Timing", 25.0),
    ("Poses / Staging", 20.0),
    ("Arcos de movimiento", 20.0),
    ("Peso y dinámica", 20.0),
    ("Pulido / Acabado", 15.0),
]


def seed_rubric_for_assignment(session: Session, assignment_id: int, course_id: int) -> None:
    """Le da a una tarea recién creada un punto de partida para su rúbrica.

    Si el curso ya tiene otra tarea con rúbrica, copia la más reciente (lo más
    probable es que el profesor quiera evaluar cosas parecidas). Si es la
    primera tarea del curso, parte con los aspectos de ejemplo.
    """
    latest_other = session.exec(
        select(Assignment)
        .where(Assignment.course_id == course_id, Assignment.id != assignment_id)
        .order_by(Assignment.created_at.desc())
    ).first()

    source_aspects = []
    if latest_other:
        source_aspects = session.exec(
            select(RubricAspect)
            .where(RubricAspect.assignment_id == latest_other.id)
            .order_by(RubricAspect.order)
        ).all()

    if source_aspects:
        for a in source_aspects:
            session.add(RubricAspect(name=a.name, weight=a.weight, order=a.order, assignment_id=assignment_id))
    else:
        for i, (name, weight) in enumerate(DEFAULT_ASPECTS):
            session.add(RubricAspect(name=name, weight=weight, order=i, assignment_id=assignment_id))
    session.commit()


def seed_defaults(session: Session) -> None:
    """Solo corre en una instalación totalmente nueva (sin ningún curso todavía).

    Si el archivo de base de datos venía de una versión anterior sin cursos,
    la migración en database.py ya se encargó de crear "Curso 1" y agrupar
    ahí los datos existentes — en ese caso esta función no hace nada.
    """
    existing_course = session.exec(select(Course)).first()
    if existing_course:
        return
    course = Course(name="Curso 1", active=True)
    session.add(course)
    session.commit()
    # Sin tareas todavía — cada tarea nueva recibe su propia rúbrica al crearse.
