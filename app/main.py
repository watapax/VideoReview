import json
import logging
import os
import secrets
import uuid
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse, RedirectResponse, Response

from app import storage
from app.auth import TEACHER_PASSWORD, hash_password, is_admin_teacher, is_logged_in, require_login, verify_password
from app.database import engine, init_db
from app.models import (
    Annotation,
    Assignment,
    Course,
    Grade,
    ReportShare,
    RubricAspect,
    Student,
    Teacher,
    Video,
    VideoShare,
)
from app.scoring import SCALE_MAX, SCALE_MIN, band, fmt, weight_display, weighted_average, weights_sum
from app.seed import seed_defaults, seed_rubric_for_assignment

logger = logging.getLogger(__name__)

_MESES_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def fmt_date_es(dt: datetime) -> str:
    return f"{dt.day} {_MESES_ES[dt.month - 1]}"


def fmt_size(num_bytes: int) -> str:
    if not num_bytes:
        return ""
    mb = num_bytes / 1_048_576
    return f"{mb:.0f} MB" if mb >= 1 else f"{num_bytes / 1024:.0f} KB"


def fmt_time_mmss(seconds: float) -> str:
    total = max(0, int(round(seconds or 0)))
    m, s = divmod(total, 60)
    return f"{m}:{s:02d}"


def get_or_create_share_token(session: Session, assignment_id: int, student_id: int) -> str:
    """El link público (/watch/{token}) es por tarea+estudiante, no por video
    individual — cubre todos los intentos/revisiones de ese par. Se crea la
    primera vez que se pide (botón "Compartir"), reutilizando el mismo token
    en cada visita siguiente en vez de generar uno nuevo cada vez."""
    share = session.exec(
        select(VideoShare).where(
            VideoShare.assignment_id == assignment_id, VideoShare.student_id == student_id
        )
    ).first()
    if share:
        return share.share_token
    share = VideoShare(assignment_id=assignment_id, student_id=student_id, share_token=secrets.token_urlsafe(16))
    session.add(share)
    session.commit()
    return share.share_token


def get_or_create_report_share_token(session: Session, assignment_id: int) -> str:
    """El link público del informe (/report/{token}) es por TAREA — el informe
    ya reúne a propósito a todo el curso (ver la intro de report.html), así
    que no hace falta un token por estudiante. Se crea la primera vez que se
    pide (botón "Compartir informe"), reutilizando el mismo token después."""
    share = session.exec(select(ReportShare).where(ReportShare.assignment_id == assignment_id)).first()
    if share:
        return share.share_token
    share = ReportShare(assignment_id=assignment_id, share_token=secrets.token_urlsafe(16))
    session.add(share)
    session.commit()
    return share.share_token

APP_DIR = os.path.dirname(__file__)

app = FastAPI(title="Corrección de Animación")
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "dev-secret-change-me"))
app.mount("/static", StaticFiles(directory=os.path.join(APP_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(APP_DIR, "templates"))

_STATIC_DIR = os.path.join(APP_DIR, "static")


def _asset_version(filename: str) -> str:
    """Cache-busting para /static/<filename>: agrega ?v=<mtime> al link en
    vez de un número fijo que haya que acordarse de subir a mano — así una
    actualización de styles.css (o cualquier estático) no se queda pegada
    en el caché del navegador del docente después de actualizar el server
    (por eso un rediseño puede verse "sin estilos" hasta hacer refresh
    forzado, si no se hace este cache-busting)."""
    try:
        return str(int(os.path.getmtime(os.path.join(_STATIC_DIR, filename))))
    except OSError:
        return "0"


templates.env.globals["asset_version"] = _asset_version


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    with Session(engine) as session:
        seed_defaults(session)


def initials(name: str) -> str:
    parts = [p for p in name.strip().split() if p]
    letters = "".join(p[0].upper() for p in parts[:2])
    return letters or "?"


def my_courses(session: Session, request: Request) -> list[Course]:
    """Cursos activos que el docente en sesión puede editar: los suyos, más
    los huérfanos sin dueño de una instalación anterior a que existieran las
    cuentas (ver can_edit_course). Son los ÚNICOS que aparecen en el
    selector "Curso" de la barra lateral y en los que se puede navegar
    (Tareas, Estudiantes, Corregir, Rúbrica, Videos) — de un curso de otro
    docente lo único visible es su informe por tarea (ver /courses, sección
    "Cursos de otros docentes", y /assignments/{id}/report)."""
    all_active = session.exec(select(Course).where(Course.active == True).order_by(Course.name)).all()  # noqa: E712
    return [c for c in all_active if can_edit_course(request, c)]


def current_course(session: Session, request: Request) -> tuple[Course, list[Course]]:
    """Curso activo de esta sesión + la lista de TUS cursos para el selector
    (ver my_courses — nunca incluye cursos de otro docente).

    Si la sesión no tiene un curso elegido (o el guardado ya no existe, está
    inactivo, o es de otro docente), elige el primero disponible entre los
    tuyos y lo deja guardado.
    """
    courses = my_courses(session, request)
    if not courses:
        course = Course(name="Mi curso", active=True, owner_teacher_id=request.session.get("teacher_id"))
        session.add(course)
        session.commit()
        session.refresh(course)
        courses = [course]

    course_id = request.session.get("course_id")
    course = next((c for c in courses if c.id == course_id), None)
    if course is None:
        course = courses[0]
        request.session["course_id"] = course.id
    return course, courses


def set_current_course(request: Request, course_id: int) -> None:
    request.session["course_id"] = course_id


def current_teacher(session: Session, request: Request) -> "Teacher | None":
    teacher_id = request.session.get("teacher_id")
    if teacher_id is None:
        return None
    return session.get(Teacher, teacher_id)


def can_edit_course(request: Request, course: Course) -> bool:
    """Todos los docentes VEN todos los cursos activos; solo el dueño puede
    editarlos. owner_teacher_id es None en cursos de una instalación previa
    a que existieran las cuentas (ver _migrate_add_course_owner en
    database.py) — esos cursos "huérfanos" nadie los reclama automáticamente
    (cada /signup nuevo crea su propio curso en blanco, ver main.py), así
    que mientras sigan sin dueño cualquier docente logueado puede editarlos,
    para no dejarlos inaccesibles."""
    if course.owner_teacher_id is None:
        return True
    return course.owner_teacher_id == request.session.get("teacher_id")


def require_course_owner(session: Session, request: Request, course: "Course | None", redirect_url: str):
    """None si se puede seguir (el curso existe y el docente en sesión puede
    editarlo); si no, el RedirectResponse que la ruta que llama debe
    devolver de inmediato. Se usa en toda ruta que MODIFICA algo de un
    curso (o de una tarea/estudiante/video/anotación que le pertenece) —
    la visibilidad es para todos los docentes, pero la edición es solo del
    dueño.

    Si el curso todavía no tiene dueño (owner_teacher_id None — instalación
    previa a que existieran las cuentas, ver _migrate_add_course_owner en
    database.py), el primer docente logueado que lo edite lo reclama: queda
    a su nombre desde ese momento, para que no siga abierto a cualquiera
    indefinidamente."""
    if course is None:
        return RedirectResponse(url="/?msg=Ese curso ya no existe.", status_code=303)
    if can_edit_course(request, course):
        if course.owner_teacher_id is None:
            teacher_id = request.session.get("teacher_id")
            if teacher_id is not None:
                course.owner_teacher_id = teacher_id
                session.add(course)
                session.commit()
        return None
    sep = "&" if "?" in redirect_url else "?"
    return RedirectResponse(
        url=f"{redirect_url}{sep}msg=No puedes editar un curso de otro docente.", status_code=303
    )


def require_own_assignment_view(session: Session, request: Request, assignment: "Assignment"):
    """De un curso de otro docente, lo único que se puede ver es su informe
    por tarea (/assignments/{id}/report, ver /courses → "Cursos de otros
    docentes"). Esta función va al principio de las pantallas de Corregir,
    Rúbrica y Ver video/anotar — que son de edición, no de solo lectura —
    para mandar a cualquiera que no sea el dueño directo al informe de esa
    tarea en vez de mostrarle el formulario. Devuelve None si puede seguir."""
    course = session.get(Course, assignment.course_id)
    if course is not None and not can_edit_course(request, course):
        return RedirectResponse(url=f"/assignments/{assignment.id}/report", status_code=303)
    return None


# ---------------------------------------------------------------- auth ----

@app.get("/login")
def login_form(request: Request):
    if is_logged_in(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None, "name": None})


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    name = (form.get("name") or "").strip()
    password = form.get("password", "")
    with Session(engine) as session:
        teacher = session.exec(select(Teacher).where(Teacher.name_lower == name.lower())).first()
    if teacher and verify_password(password, teacher.password_hash):
        request.session["teacher_id"] = teacher.id
        request.session["teacher_name"] = teacher.name
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Nombre o contraseña incorrectos.", "name": name},
        status_code=401,
    )


@app.get("/signup")
def signup_form(request: Request):
    if is_logged_in(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("signup.html", {"request": request, "error": None, "name": None})


@app.post("/signup")
async def signup_submit(request: Request):
    form = await request.form()
    name = (form.get("name") or "").strip()
    password = form.get("password") or ""
    invite_code = form.get("invite_code") or ""

    def error(msg: str):
        return templates.TemplateResponse(
            "signup.html", {"request": request, "error": msg, "name": name}, status_code=400
        )

    if invite_code != TEACHER_PASSWORD:
        return error("Código de invitación incorrecto.")
    if not name:
        return error("Escribe tu nombre.")
    if len(password) < 4:
        return error("La contraseña debe tener al menos 4 caracteres.")

    with Session(engine) as session:
        existing = session.exec(select(Teacher).where(Teacher.name_lower == name.lower())).first()
        if existing:
            return error("Ya existe una cuenta con ese nombre — si es tuya, inicia sesión.")

        teacher = Teacher(name=name, name_lower=name.lower(), password_hash=hash_password(password))
        session.add(teacher)
        session.commit()
        session.refresh(teacher)

        # Todo docente nuevo parte con un curso propio, en blanco (sin
        # estudiantes ni tareas) para que le cambie el nombre y agregue sus
        # alumnos — nunca hereda cursos ni datos de otros docentes, ni
        # siquiera cursos de una instalación anterior a que existieran las
        # cuentas (esos quedan con owner_teacher_id NULL, ver
        # _migrate_add_course_owner en database.py; can_edit_course permite
        # que cualquier docente logueado los edite mientras nadie los
        # reclame, pero /signup ya no se los asigna a nadie automáticamente).
        new_course = Course(name="Mi curso", active=True, owner_teacher_id=teacher.id)
        session.add(new_course)
        session.commit()
        session.refresh(new_course)

        request.session["teacher_id"] = teacher.id
        request.session["teacher_name"] = teacher.name
        request.session["course_id"] = new_course.id

    return RedirectResponse(url="/", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# ----------------------------------------------------------- dashboard ----

@app.get("/")
def dashboard(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    with Session(engine) as session:
        course, courses = current_course(session, request)

        assignments = session.exec(
            select(Assignment).where(Assignment.course_id == course.id).order_by(Assignment.created_at.desc())
        ).all()
        students = session.exec(
            select(Student).where(Student.course_id == course.id, Student.active == True)  # noqa: E712
        ).all()
        total_students = len(students)

        rows = []
        for a in assignments:
            aspects = session.exec(select(RubricAspect).where(RubricAspect.assignment_id == a.id)).all()
            aspects_ok = len(aspects) > 0 and abs(weights_sum([asp.weight for asp in aspects]) - 100) < 0.05
            aspect_ids = {asp.id for asp in aspects}

            grades = session.exec(select(Grade).where(Grade.assignment_id == a.id)).all()
            done_by_student: dict[int, set[int]] = {}
            for g in grades:
                if g.score is not None:
                    done_by_student.setdefault(g.student_id, set()).add(g.aspect_id)
            graded = sum(1 for done in done_by_student.values() if aspect_ids and aspect_ids.issubset(done))
            pct = round(100 * graded / total_students) if total_students else 0
            rows.append(
                {"assignment": a, "graded": graded, "total": total_students, "pct": pct, "aspects_ok": aspects_ok}
            )

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "active": "dashboard",
            "assignments": rows,
            "course": course,
            "courses": courses,
            "can_edit": can_edit_course(request, course),
        },
    )


@app.get("/assignments/new")
def assignment_new_form(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    with Session(engine) as session:
        course, courses = current_course(session, request)
    return templates.TemplateResponse(
        "assignment_new.html", {"request": request, "active": "dashboard", "course": course, "courses": courses}
    )


@app.post("/assignments/new")
async def assignment_new_submit(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    form = await request.form()
    name = (form.get("name") or "").strip()
    with Session(engine) as session:
        course, _courses = current_course(session, request)
        denied = require_course_owner(session, request, course, "/")
        if denied:
            return denied
        if name:
            a = Assignment(name=name, course_id=course.id)
            session.add(a)
            session.commit()
            session.refresh(a)
            seed_rubric_for_assignment(session, a.id, course.id)
    return RedirectResponse(url="/", status_code=303)


# -------------------------------------------------------------- cursos ----

@app.get("/courses")
def courses_list(request: Request):
    """"Mis cursos": lista editable de siempre (renombrar/activar). "Cursos
    de otros docentes": lista de solo lectura, agrupada por docente, con
    cada tarea del curso linkeando directo a su informe — es lo único que
    se puede ver de un curso ajeno (ver require_own_assignment_view)."""
    redirect = require_login(request)
    if redirect:
        return redirect
    with Session(engine) as session:
        course, courses = current_course(session, request)
        all_courses = session.exec(select(Course).order_by(Course.name)).all()

        my_rows = []
        # Agrupado por teacher_id (no por nombre): un curso solo cae en este
        # "otros" si can_edit_course dio False, y eso solo pasa cuando
        # owner_teacher_id apunta a OTRO docente real (los huérfanos, con
        # owner_teacher_id None, siempre se pueden editar -- ver
        # can_edit_course) -- así que acá el id nunca es None y sirve para el
        # botón de "eliminar docente" (ver /teachers/{id}/delete).
        other_by_teacher: dict[int, dict] = {}
        for c in all_courses:
            n_students = len(
                session.exec(
                    select(Student).where(Student.course_id == c.id, Student.active == True)  # noqa: E712
                ).all()
            )
            if can_edit_course(request, c):
                n_assignments = len(session.exec(select(Assignment).where(Assignment.course_id == c.id)).all())
                my_rows.append({"course": c, "n_students": n_students, "n_assignments": n_assignments})
            else:
                assignments = session.exec(
                    select(Assignment).where(Assignment.course_id == c.id).order_by(Assignment.created_at.desc())
                ).all()
                owner = session.get(Teacher, c.owner_teacher_id) if c.owner_teacher_id else None
                owner_name = owner.name if owner else "Otro docente"
                group = other_by_teacher.setdefault(
                    c.owner_teacher_id, {"teacher_id": c.owner_teacher_id, "teacher_name": owner_name, "rows": []}
                )
                group["rows"].append({"course": c, "n_students": n_students, "assignments": assignments})

        other_groups = []
        for _teacher_id, group in sorted(other_by_teacher.items(), key=lambda kv: kv[1]["teacher_name"].lower()):
            group["rows"].sort(key=lambda r: r["course"].name.lower())
            group["teacher_initials"] = initials(group["teacher_name"])
            other_groups.append(group)

    return templates.TemplateResponse(
        "courses.html",
        {
            "request": request,
            "active": "courses",
            "course": course,
            "courses": courses,
            "my_rows": my_rows,
            "other_groups": other_groups,
            "is_admin": is_admin_teacher(request),
        },
    )


@app.post("/courses/new")
async def courses_new(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    form = await request.form()
    name = (form.get("name") or "").strip()
    with Session(engine) as session:
        if name:
            c = Course(name=name, active=True, owner_teacher_id=request.session.get("teacher_id"))
            session.add(c)
            session.commit()
            session.refresh(c)
            set_current_course(request, c.id)
    return RedirectResponse(url="/courses", status_code=303)


@app.post("/courses/{course_id}/rename")
async def courses_rename(course_id: int, request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    form = await request.form()
    name = (form.get("name") or "").strip()
    with Session(engine) as session:
        c = session.get(Course, course_id)
        denied = require_course_owner(session, request, c, "/courses")
        if denied:
            return denied
        if c and name:
            c.name = name
            session.add(c)
            session.commit()
    return RedirectResponse(url="/courses", status_code=303)


@app.post("/courses/{course_id}/toggle")
def courses_toggle(course_id: int, request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    with Session(engine) as session:
        c = session.get(Course, course_id)
        denied = require_course_owner(session, request, c, "/courses")
        if denied:
            return denied
        if c:
            if c.active:
                active_count = len(session.exec(select(Course).where(Course.active == True)).all())  # noqa: E712
                if active_count <= 1:
                    return RedirectResponse(
                        url="/courses?msg=No puedes desactivar el único curso activo.", status_code=303
                    )
            c.active = not c.active
            session.add(c)
            session.commit()
    return RedirectResponse(url="/courses", status_code=303)


@app.post("/courses/{course_id}/delete")
def courses_delete(course_id: int, request: Request):
    """Elimina un curso por completo -- estudiantes, tareas, rúbrica, notas,
    videos (y sus anotaciones/links compartidos) y el link del informe. No
    hay forma de deshacerlo (por eso el botón pide confirmar en el navegador,
    ver courses.html), a diferencia de "Desactivar" que solo lo oculta pero
    conserva todo. Si era el único curso del docente, current_course() le
    crea uno nuevo en blanco solo, la próxima vez que abra cualquier
    pantalla -- no hace falta manejarlo acá."""
    redirect = require_login(request)
    if redirect:
        return redirect
    with Session(engine) as session:
        c = session.get(Course, course_id)
        denied = require_course_owner(session, request, c, "/courses")
        if denied:
            return denied
        if not c:
            return RedirectResponse(url="/courses", status_code=303)

        assignment_ids = [
            a.id for a in session.exec(select(Assignment).where(Assignment.course_id == course_id)).all()
        ]
        video_keys: list[str] = []

        if assignment_ids:
            videos = session.exec(select(Video).where(Video.assignment_id.in_(assignment_ids))).all()
            video_ids = [v.id for v in videos]
            video_keys = [v.object_key for v in videos]

            if video_ids:
                for a in session.exec(select(Annotation).where(Annotation.video_id.in_(video_ids))).all():
                    session.delete(a)
            for vs in session.exec(select(VideoShare).where(VideoShare.assignment_id.in_(assignment_ids))).all():
                session.delete(vs)
            for rs in session.exec(select(ReportShare).where(ReportShare.assignment_id.in_(assignment_ids))).all():
                session.delete(rs)
            for v in videos:
                session.delete(v)
            for g in session.exec(select(Grade).where(Grade.assignment_id.in_(assignment_ids))).all():
                session.delete(g)
            for asp in session.exec(select(RubricAspect).where(RubricAspect.assignment_id.in_(assignment_ids))).all():
                session.delete(asp)
            for a in session.exec(select(Assignment).where(Assignment.course_id == course_id)).all():
                session.delete(a)

        for s in session.exec(select(Student).where(Student.course_id == course_id)).all():
            session.delete(s)

        course_name = c.name
        session.delete(c)
        session.commit()

    # El video en sí (en R2) se borra DESPUÉS de confirmar que la fila de la
    # base de datos ya quedó eliminada -- mismo orden que /videos/{id}/delete,
    # para no dejar filas huérfanas si el borrado en R2 fallara a mitad de
    # camino.
    if video_keys and storage.is_configured():
        for key in video_keys:
            storage.delete_object(key)

    return RedirectResponse(url=f"/courses?msg=Curso «{course_name}» eliminado.", status_code=303)


# --------------------------------------------------------------- teachers --

@app.post("/teachers/{teacher_id}/delete")
def teachers_delete(teacher_id: int, request: Request):
    """Elimina la CUENTA de un docente (no sus cursos ni sus datos): los
    cursos que le pertenecían quedan sin dueño (owner_teacher_id = None),
    exactamente como los de una instalación anterior a que existieran las
    cuentas -- el próximo docente que los edite los reclama (ver
    require_course_owner). Así se puede borrar una cuenta duplicada o de
    alguien que ya no participa sin perder ningún estudiante, nota o video.

    Solo la cuenta administradora (ver ADMIN_TEACHER_NAME en auth.py) puede
    hacerlo -- antes cualquier docente logueado podía eliminar a cualquier
    otro, lo cual abría la puerta a que alguien borrara cuentas ajenas sin
    ningún control. Tampoco se puede eliminar la propia cuenta, para no
    dejar la sesión activa apuntando a un teacher_id que ya no existe."""
    redirect = require_login(request)
    if redirect:
        return redirect
    if not is_admin_teacher(request):
        return RedirectResponse(
            url="/courses?msg=Solo la cuenta administradora puede eliminar docentes.", status_code=303
        )
    if teacher_id == request.session.get("teacher_id"):
        return RedirectResponse(url="/courses?msg=No puedes eliminar tu propia cuenta.", status_code=303)
    with Session(engine) as session:
        teacher = session.get(Teacher, teacher_id)
        if not teacher:
            return RedirectResponse(url="/courses", status_code=303)
        teacher_name = teacher.name
        for c in session.exec(select(Course).where(Course.owner_teacher_id == teacher_id)).all():
            c.owner_teacher_id = None
            session.add(c)
        session.delete(teacher)
        session.commit()
    return RedirectResponse(
        url=f"/courses?msg=Docente «{teacher_name}» eliminado. Sus cursos quedaron sin dueño.", status_code=303
    )


@app.get("/courses/switch")
def courses_switch(request: Request, course_id: int):
    redirect = require_login(request)
    if redirect:
        return redirect
    with Session(engine) as session:
        c = session.get(Course, course_id)
        # Solo se puede cambiar el curso activo de la barra lateral a uno
        # propio (o huérfano) — un curso de otro docente nunca se navega,
        # solo se ve su informe por tarea (ver /courses).
        if c and c.active and can_edit_course(request, c):
            set_current_course(request, c.id)
    return RedirectResponse(url="/", status_code=303)


# -------------------------------------------------------------- rubric ----

@app.get("/assignments/{assignment_id}/rubric")
def rubric_form(assignment_id: int, request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    with Session(engine) as session:
        assignment = session.get(Assignment, assignment_id)
        if not assignment:
            return RedirectResponse(url="/?msg=Esa tarea no existe.", status_code=303)

        redirect_ro = require_own_assignment_view(session, request, assignment)
        if redirect_ro:
            return redirect_ro

        set_current_course(request, assignment.course_id)
        course, courses = current_course(session, request)

        aspects = session.exec(
            select(RubricAspect).where(RubricAspect.assignment_id == assignment.id).order_by(RubricAspect.order)
        ).all()
    return templates.TemplateResponse(
        "rubric.html",
        {
            "request": request,
            "active": "dashboard",
            "assignment": assignment,
            "aspects": aspects,
            "error": None,
            "course": course,
            "courses": courses,
            "can_edit": can_edit_course(request, course),
        },
    )


@app.post("/assignments/{assignment_id}/rubric")
async def rubric_submit(assignment_id: int, request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    form = await request.form()
    ids = form.getlist("aspect_id")
    names = form.getlist("aspect_name")
    weights = form.getlist("aspect_weight")
    descriptions = form.getlist("aspect_description")
    # aspect_description puede venir vacío para filas viejas que no tenían el
    # campo (no debería pasar con el template actual, pero por si acaso no
    # queremos que un desalineamiento de listas rompa el zip).
    if len(descriptions) < len(ids):
        descriptions += [""] * (len(ids) - len(descriptions))
    rows = list(zip(ids, names, weights, descriptions))

    with Session(engine) as session:
        assignment = session.get(Assignment, assignment_id)
        if not assignment:
            return RedirectResponse(url="/?msg=Esa tarea no existe.", status_code=303)

        set_current_course(request, assignment.course_id)
        course, courses = current_course(session, request)
        denied = require_course_owner(session, request, course, f"/assignments/{assignment_id}/rubric")
        if denied:
            return denied

        def rerender(error: str):
            submitted = [
                {"id": rid, "name": rname, "weight": rweight, "description": rdesc}
                for rid, rname, rweight, rdesc in rows
            ]
            return templates.TemplateResponse(
                "rubric.html",
                {
                    "request": request,
                    "active": "dashboard",
                    "assignment": assignment,
                    "aspects": submitted,
                    "error": error,
                    "course": course,
                    "courses": courses,
                },
                status_code=400,
            )

        try:
            total = sum(float(w) for _id, _name, w, _desc in rows)
        except ValueError:
            return rerender("Alguna ponderación no es un número válido.")

        if not rows:
            return rerender("Necesitas al menos un aspecto en la rúbrica.")
        if abs(total - 100) > 0.5:
            return rerender(f"Las ponderaciones deben sumar 100% (ahora suman {total:g}%).")

        existing = session.exec(
            select(RubricAspect).where(RubricAspect.assignment_id == assignment.id)
        ).all()
        submitted_ids = {int(rid) for rid, _n, _w, _d in rows if rid != "new"}
        to_delete = [a for a in existing if a.id not in submitted_ids]

        for a in to_delete:
            has_grades = session.exec(select(Grade).where(Grade.aspect_id == a.id)).first()
            if has_grades:
                return rerender(
                    f"No se puede quitar «{a.name}»: ya tiene notas guardadas para esta tarea. "
                    "Puedes cambiarle el nombre o dejar su ponderación en 0% en vez de quitarlo."
                )

        for a in to_delete:
            session.delete(a)

        for idx, (rid, rname, rweight, rdesc) in enumerate(rows):
            rname = rname.strip()
            rweight_f = float(rweight)
            rdesc_clean = rdesc.strip() or None
            if rid == "new":
                session.add(
                    RubricAspect(
                        name=rname, weight=rweight_f, order=idx, assignment_id=assignment.id, description=rdesc_clean
                    )
                )
            else:
                obj = session.get(RubricAspect, int(rid))
                if obj and obj.assignment_id == assignment.id:
                    obj.name = rname
                    obj.weight = rweight_f
                    obj.order = idx
                    obj.description = rdesc_clean
                    session.add(obj)

        session.commit()

    return RedirectResponse(
        url=f"/assignments/{assignment_id}/rubric?msg=Rúbrica actualizada.", status_code=303
    )


# ------------------------------------------------------------ students ----

@app.get("/students")
def students_list(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    with Session(engine) as session:
        course, courses = current_course(session, request)
        students = session.exec(
            select(Student).where(Student.course_id == course.id).order_by(Student.name)
        ).all()
    return templates.TemplateResponse(
        "students.html",
        {
            "request": request,
            "active": "students",
            "students": students,
            "course": course,
            "courses": courses,
            "can_edit": can_edit_course(request, course),
        },
    )


@app.post("/students/add")
async def students_add(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    form = await request.form()
    name = (form.get("name") or "").strip()
    with Session(engine) as session:
        course, _courses = current_course(session, request)
        denied = require_course_owner(session, request, course, "/students")
        if denied:
            return denied
        if name:
            session.add(Student(name=name, course_id=course.id))
            session.commit()
    return RedirectResponse(url="/students", status_code=303)


@app.post("/students/bulk_add")
async def students_bulk_add(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    form = await request.form()
    names_raw = form.get("names") or ""
    names = [n.strip() for n in names_raw.splitlines() if n.strip()]
    with Session(engine) as session:
        course, _courses = current_course(session, request)
        denied = require_course_owner(session, request, course, "/students")
        if denied:
            return denied
        for n in names:
            session.add(Student(name=n, course_id=course.id))
        if names:
            session.commit()
    return RedirectResponse(url="/students", status_code=303)


@app.post("/students/{student_id}/toggle")
def students_toggle(student_id: int, request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    with Session(engine) as session:
        s = session.get(Student, student_id)
        if s is None:
            return RedirectResponse(url="/students?msg=Ese estudiante ya no existe.", status_code=303)
        course = session.get(Course, s.course_id)
        denied = require_course_owner(session, request, course, "/students")
        if denied:
            return denied
        s.active = not s.active
        session.add(s)
        session.commit()
    return RedirectResponse(url="/students", status_code=303)


# --------------------------------------------------------- grading UI ----

@app.get("/assignments/{assignment_id}")
def grading_screen(assignment_id: int, request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    student_id = request.query_params.get("student_id")
    student_id = int(student_id) if student_id else None

    with Session(engine) as session:
        assignment = session.get(Assignment, assignment_id)
        if not assignment:
            return RedirectResponse(url="/?msg=Esa tarea no existe.", status_code=303)

        redirect_ro = require_own_assignment_view(session, request, assignment)
        if redirect_ro:
            return redirect_ro

        # Al abrir una tarea, el curso activo de la sesión pasa a ser el de esa tarea
        # (así el resto de la navegación - Estudiantes, Rúbrica - queda consistente).
        set_current_course(request, assignment.course_id)
        course, courses = current_course(session, request)

        aspects = session.exec(
            select(RubricAspect).where(RubricAspect.assignment_id == assignment.id).order_by(RubricAspect.order)
        ).all()
        aspects_ctx = [
            {"id": a.id, "name": a.name, "weight": a.weight, "weight_display": weight_display(a.weight)}
            for a in aspects
        ]
        aspect_ids = [a.id for a in aspects if a.weight > 0]

        students_all = session.exec(
            select(Student)
            .where(Student.course_id == assignment.course_id, Student.active == True)  # noqa: E712
            .order_by(Student.name)
        ).all()

        grades_all = session.exec(select(Grade).where(Grade.assignment_id == assignment_id)).all()
        grades_by_student: dict[int, dict[int, Grade]] = {}
        for g in grades_all:
            grades_by_student.setdefault(g.student_id, {})[g.aspect_id] = g

        students_ctx = []
        for s in students_all:
            done = [
                aid for aid in aspect_ids
                if grades_by_student.get(s.id, {}).get(aid) and grades_by_student[s.id][aid].score is not None
            ]
            if not done:
                status_label = "Pendiente"
            elif len(done) == len(aspect_ids) and aspect_ids:
                status_label = "Calificado"
            else:
                status_label = f"En progreso ({len(done)}/{len(aspect_ids)})"
            students_ctx.append({"id": s.id, "name": s.name, "status": status_label, "initials": initials(s.name)})

        graded_count = sum(1 for s in students_ctx if s["status"] == "Calificado")

        current = None
        if student_id is not None:
            current = next((s for s in students_ctx if s["id"] == student_id), None)
        if current is None and students_ctx:
            current = students_ctx[0]

        current_grades = grades_by_student.get(current["id"], {}) if current else {}

        next_student = None
        if current:
            idx = next(i for i, s in enumerate(students_ctx) if s["id"] == current["id"])
            if idx + 1 < len(students_ctx):
                next_student = students_ctx[idx + 1]

        videos_ctx = []
        videos_share_url = None
        if current:
            videos = session.exec(
                select(Video)
                .where(Video.assignment_id == assignment_id, Video.student_id == current["id"])
                .order_by(Video.uploaded_at)
            ).all()
            aspect_names_by_id = {a["id"]: a["name"] for a in aspects_ctx}
            for v in videos:
                ann_count = len(session.exec(select(Annotation).where(Annotation.video_id == v.id)).all())
                videos_ctx.append(
                    {
                        "id": v.id,
                        "label": v.label or v.original_filename or "Video",
                        "size_display": fmt_size(v.size_bytes),
                        "uploaded_display": fmt_date_es(v.uploaded_at),
                        "annotation_count": ann_count,
                        "rubric_aspect_id": v.rubric_aspect_id,
                        "aspect_name": aspect_names_by_id.get(v.rubric_aspect_id),
                    }
                )
            # Un solo link por tarea+estudiante (no por video: puede haber más
            # de una revisión/intento) — el estudiante navega entre ellas
            # dentro de la página pública.
            if videos:
                token = get_or_create_share_token(session, assignment_id, current["id"])
                videos_share_url = str(request.base_url).rstrip("/") + f"/watch/{token}"

        # get_or_create_share_token puede hacer session.commit() (si crea el
        # token por primera vez), lo que expira los atributos ya cargados de
        # assignment/course/courses en esta sesión — por eso el render de la
        # plantilla (que los lee) tiene que quedar DENTRO de este `with`.
        active_tab = "videos" if request.query_params.get("tab") == "videos" else "rubrica"

        return templates.TemplateResponse(
            "grading.html",
            {
                "request": request,
                "assignment": assignment,
                "students": students_ctx,
                "current": current,
                "aspects": aspects_ctx,
                "grades": current_grades,
                "graded_count": graded_count,
                "next_student": next_student,
                "scale_min": SCALE_MIN,
                "scale_max": SCALE_MAX,
                "scale_min_display": str(int(SCALE_MIN)),
                "scale_max_display": str(int(SCALE_MAX)),
                "course": course,
                "courses": courses,
                "videos": videos_ctx,
                "videos_share_url": videos_share_url,
                "active_tab": active_tab,
                "r2_configured": storage.is_configured(),
                "can_edit": can_edit_course(request, course),
            },
        )


@app.post("/assignments/{assignment_id}/grade")
async def grading_save(assignment_id: int, request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    student_id = request.query_params.get("student_id")
    next_id = request.query_params.get("next")
    if not student_id:
        return RedirectResponse(url=f"/assignments/{assignment_id}", status_code=303)
    student_id = int(student_id)

    form = await request.form()

    with Session(engine) as session:
        assignment = session.get(Assignment, assignment_id)
        if not assignment:
            return RedirectResponse(url="/?msg=Esa tarea no existe.", status_code=303)
        course = session.get(Course, assignment.course_id)
        denied = require_course_owner(session, request, course, f"/assignments/{assignment_id}")
        if denied:
            return denied

        aspects = session.exec(select(RubricAspect).where(RubricAspect.assignment_id == assignment.id)).all()
        for a in aspects:
            score_raw = form.get(f"score_{a.id}")
            feedback = (form.get(f"feedback_{a.id}") or "").strip()
            score = None
            if score_raw not in (None, ""):
                try:
                    score = float(score_raw)
                    score = max(SCALE_MIN, min(SCALE_MAX, score))
                except ValueError:
                    score = None

            existing = session.exec(
                select(Grade).where(
                    Grade.assignment_id == assignment_id,
                    Grade.student_id == student_id,
                    Grade.aspect_id == a.id,
                )
            ).first()
            if existing:
                existing.score = score
                existing.feedback = feedback
                session.add(existing)
            else:
                session.add(
                    Grade(
                        assignment_id=assignment_id,
                        student_id=student_id,
                        aspect_id=a.id,
                        score=score,
                        feedback=feedback,
                    )
                )
        session.commit()

    target_student = next_id if next_id else student_id
    return RedirectResponse(url=f"/assignments/{assignment_id}?student_id={target_student}", status_code=303)


# -------------------------------------------------------------- report ----
# El informe de una tarea (resumen de notas + feedback por aspecto, de TODO
# el curso a la vez) se arma igual para el profesor (logueado, con el botón
# para compartirlo) que para la versión pública sin contraseña — por eso
# ambas rutas comparten _report_ctx(). En "Resumen de notas finales" cada
# estudiante que tenga al menos un video en esta tarea muestra además un
# botón al link público de sus anotaciones de video (mismo mecanismo que
# get_or_create_share_token, ver arriba).


def _report_ctx(session: Session, assignment: Assignment, base_url: str):
    """Arma los datos del informe para UN estudiante a la vez (no ya por
    aspecto): cada estudiante trae su nota final, sus aspectos (con nota y
    feedback) y sus videos con anotaciones — todo lo que necesita la vista
    de detalle y la vista de video del informe (fase 9), que se abren sin
    salir de la página. `aspects_meta` (nombre + ponderación) va aparte
    porque es igual para todos los estudiantes.
    """
    aspects = session.exec(
        select(RubricAspect).where(RubricAspect.assignment_id == assignment.id).order_by(RubricAspect.order)
    ).all()
    students = session.exec(
        select(Student)
        .where(Student.course_id == assignment.course_id, Student.active == True)  # noqa: E712
        .order_by(Student.name)
    ).all()
    grades_all = session.exec(select(Grade).where(Grade.assignment_id == assignment.id)).all()
    videos_all = session.exec(
        select(Video).where(Video.assignment_id == assignment.id).order_by(Video.uploaded_at)
    ).all()

    videos_by_student: dict[int, list[Video]] = {}
    for v in videos_all:
        videos_by_student.setdefault(v.student_id, []).append(v)

    annotations_by_video: dict[int, list[Annotation]] = {}
    video_ids = [v.id for v in videos_all]
    if video_ids:
        annotations_all = session.exec(
            select(Annotation).where(Annotation.video_id.in_(video_ids)).order_by(Annotation.time_seconds)
        ).all()
        for a in annotations_all:
            annotations_by_video.setdefault(a.video_id, []).append(a)

    grades_by_student: dict[int, dict[int, Grade]] = {}
    for g in grades_all:
        grades_by_student.setdefault(g.student_id, {})[g.aspect_id] = g

    finals: dict[int, float | None] = {}
    for s in students:
        pairs = []
        for a in aspects:
            g = grades_by_student.get(s.id, {}).get(a.id)
            pairs.append((g.score if g else None, a.weight))
        finals[s.id] = weighted_average(pairs)

    ordered_students = sorted(
        students, key=lambda s: (finals[s.id] is None, -(finals[s.id] or 0), s.name)
    )

    aspects_meta = [
        {
            "name": a.name,
            "weight_display": weight_display(a.weight),
            "description": (a.description or "").strip(),
        }
        for a in aspects
    ]

    students_ctx = []
    for s in ordered_students:
        grade = finals[s.id]
        pct = 0.0
        if grade is not None:
            pct = max(0.0, min(100.0, (grade - SCALE_MIN) / (SCALE_MAX - SCALE_MIN) * 100))

        student_videos = videos_by_student.get(s.id, [])
        # aspecto -> índice (en s.videos, ver más abajo) del primer video
        # vinculado a él (Video.rubric_aspect_id) -- así la tarjeta de ese
        # aspecto en el informe puede ofrecer "Ver video" directo, sin que el
        # profesor tenga que adivinar cuál de los videos subidos corresponde.
        video_index_by_aspect: dict[int, int] = {}
        for idx, v in enumerate(student_videos):
            if v.rubric_aspect_id is not None and v.rubric_aspect_id not in video_index_by_aspect:
                video_index_by_aspect[v.rubric_aspect_id] = idx

        student_aspects = []
        for a in aspects:
            g = grades_by_student.get(s.id, {}).get(a.id)
            score = g.score if g else None
            student_aspects.append(
                {
                    "score_display": fmt(score) if score is not None else "—",
                    "band": band(score),
                    "feedback": g.feedback if g else "",
                    "video_index": video_index_by_aspect.get(a.id),
                }
            )

        videos_ctx = []
        if student_videos:
            # Mismo token que usaba el botón "Videos" de antes -- ya sirve
            # tanto para el profesor logueado como para el link público del
            # informe, así que el reproductor embebido en el informe usa la
            # misma ruta de streaming pública (/watch/{token}/stream) en
            # ambos casos, sin necesitar dos mecanismos distintos.
            token = get_or_create_share_token(session, assignment.id, s.id)
            stream_base = base_url + f"/watch/{token}/stream"
            for v in student_videos:
                anns = annotations_by_video.get(v.id, [])
                videos_ctx.append(
                    {
                        "id": v.id,
                        "label": v.label,
                        "stream_url": stream_base + f"?v={v.id}",
                        "annotations": [
                            {
                                "id": a.id,
                                "time_seconds": a.time_seconds,
                                "end_time_seconds": a.end_time_seconds,
                                "color": a.color if a.color in ALLOWED_ANNOTATION_COLORS else "#5b7cfa",
                                "drawing_data": a.drawing_data,
                                "note": a.note,
                            }
                            for a in anns
                        ],
                    }
                )

        students_ctx.append(
            {
                "id": s.id,
                "name": s.name,
                "grade_display": fmt(grade),
                "pct": round(pct, 1),
                "band": band(grade),
                "aspects": student_aspects,
                "videos": videos_ctx,
            }
        )

    return students_ctx, aspects_meta


@app.get("/assignments/{assignment_id}/report")
def report(assignment_id: int, request: Request):
    """El informe es la ÚNICA pantalla de un curso ajeno que un docente
    puede abrir (ver require_own_assignment_view y la sección "Cursos de
    otros docentes" en /courses) — por eso, a diferencia del resto de
    rutas, no depende de current_course() para saber de qué curso mostrar
    datos (ese siempre ignora los cursos ajenos): el curso se busca
    directo por assignment.course_id. Cuando SÍ es tu curso, además se deja
    la barra lateral apuntando ahí, como antes; cuando es de otro docente
    no se toca la selección de curso de la sesión, y no se genera el link
    público para compartir ni se muestra el botón (no es tuyo para
    compartirlo)."""
    redirect = require_login(request)
    if redirect:
        return redirect

    with Session(engine) as session:
        assignment = session.get(Assignment, assignment_id)
        if not assignment:
            return RedirectResponse(url="/?msg=Esa tarea no existe.", status_code=303)

        report_course = session.get(Course, assignment.course_id)
        can_edit = can_edit_course(request, report_course) if report_course else False
        owner_name = None
        if report_course and not can_edit and report_course.owner_teacher_id:
            owner = session.get(Teacher, report_course.owner_teacher_id)
            owner_name = owner.name if owner else None
        if can_edit:
            set_current_course(request, assignment.course_id)

        base_url = str(request.base_url).rstrip("/")
        students_ctx, aspects_meta = _report_ctx(session, assignment, base_url)
        report_share_url = None
        if can_edit:
            report_share_url = base_url + f"/report/{get_or_create_report_share_token(session, assignment.id)}"

        # students_ctx completo (aspectos + videos + anotaciones) va como
        # JSON embebido para que el detalle y el video de cada estudiante se
        # puedan mostrar sin salir de la página (fase 9) — mismo patrón que
        # annotations_json en video_review(). El reemplazo de "</" evita que
        # una nota o feedback con ese texto cierre el <script> antes de tiempo.
        report_data_json = json.dumps({"students": students_ctx, "aspects": aspects_meta}).replace("</", "<\\/")

        # _report_ctx / get_or_create_report_share_token pueden hacer
        # session.commit() (la primera vez que se crea un token), lo que
        # expira los atributos ya cargados de assignment/course en esta
        # sesión — por eso el render de la plantilla queda DENTRO de este
        # `with`, nunca después de que la sesión se cierre (mismo motivo que
        # en grading_screen/video_review — ver los comentarios ahí).
        return templates.TemplateResponse(
            "report.html",
            {
                "request": request,
                "assignment": assignment,
                "course_name": report_course.name if report_course else "",
                "students": students_ctx,
                "report_data_json": report_data_json,
                "scale_min_display": str(int(SCALE_MIN)),
                "scale_max_display": str(int(SCALE_MAX)),
                "report_share_url": report_share_url,
                "can_edit": can_edit,
                "owner_name": owner_name,
                "is_teacher_view": True,
            },
        )


@app.get("/report/{share_token}")
def public_report(share_token: str, request: Request):
    """Versión de solo lectura del informe, sin contraseña — para que el
    curso completo lo pueda revisar sin entrar al panel del profesor. Mismo
    contenido que la vista logueada (ver _report_ctx), sin el botón
    "Compartir informe" (no tiene sentido re-compartir desde ahí)."""
    with Session(engine) as session:
        share = session.exec(select(ReportShare).where(ReportShare.share_token == share_token)).first()
        assignment = session.get(Assignment, share.assignment_id) if share else None
        if not assignment:
            return templates.TemplateResponse("public_not_found.html", {"request": request}, status_code=404)

        course = session.get(Course, assignment.course_id)

        base_url = str(request.base_url).rstrip("/")
        students_ctx, aspects_meta = _report_ctx(session, assignment, base_url)
        report_data_json = json.dumps({"students": students_ctx, "aspects": aspects_meta}).replace("</", "<\\/")

        return templates.TemplateResponse(
            "report.html",
            {
                "request": request,
                "assignment": assignment,
                "course_name": course.name if course else "",
                "students": students_ctx,
                "report_data_json": report_data_json,
                "scale_min_display": str(int(SCALE_MIN)),
                "scale_max_display": str(int(SCALE_MAX)),
                "report_share_url": None,
                "is_teacher_view": False,
            },
        )


# --------------------------------------------------------------- videos ----
# Fase 1 de revisión de video: subir clips .mp4 (por estudiante + tarea) y
# verlos con los controles de tiempo nativos del navegador. Todavía sin
# dibujo/anotaciones (esa es la fase 2). El archivo se guarda en Cloudflare
# R2 — acá solo queda la referencia en la base de datos.

ALLOWED_VIDEO_EXTENSIONS = (".mp4",)


def _is_valid_mp4(filename: str, header: bytes) -> bool:
    """Sube solo .mp4: revisa la extensión Y los primeros bytes del archivo
    (los mp4 parten con un box "ftyp" en el byte 4) — así se rechaza, por
    ejemplo, un .mkv que alguien haya renombrado a mano a .mp4."""
    if not filename or not filename.lower().endswith(ALLOWED_VIDEO_EXTENSIONS):
        return False
    return len(header) >= 8 and header[4:8] == b"ftyp"


@app.post("/assignments/{assignment_id}/videos")
async def video_upload(assignment_id: int, request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    student_id_raw = request.query_params.get("student_id")
    if not student_id_raw:
        return RedirectResponse(url=f"/assignments/{assignment_id}", status_code=303)
    student_id = int(student_id_raw)

    def back(msg: str, status_code: int = 303):
        return RedirectResponse(
            url=f"/assignments/{assignment_id}?student_id={student_id}&tab=videos&msg={msg}",
            status_code=status_code,
        )

    if not storage.is_configured():
        return back("El almacenamiento de video (Cloudflare R2) no está configurado todavía en este servidor.")

    form = await request.form()
    # "files" (con `multiple` en el input) es el nombre nuevo; "file" queda
    # como respaldo por si algún formulario viejo en caché todavía manda el
    # campo singular de antes de que se pudiera subir más de uno a la vez.
    uploads = [f for f in form.getlist("files") if getattr(f, "filename", "")]
    if not uploads:
        legacy = form.get("file")
        if legacy is not None and getattr(legacy, "filename", ""):
            uploads = [legacy]
    label = (form.get("label") or "").strip()

    if not uploads:
        return back("Elige uno o más archivos de video.")

    # Valida TODOS los archivos antes de subir cualquiera — así, si uno de
    # varios no es un .mp4 válido, no queda una subida a medias (algunos
    # videos sí y otros no) sin que quede claro cuál falló.
    invalid = []
    for upload in uploads:
        header = await upload.read(12)
        await upload.seek(0)
        if not _is_valid_mp4(upload.filename, header):
            invalid.append(upload.filename)
    if invalid:
        return back(
            "Solo se aceptan videos en formato .mp4 — no se subió nada porque "
            + ("este archivo no es válido: " if len(invalid) == 1 else "estos archivos no son válidos: ")
            + ", ".join(invalid)
        )

    with Session(engine) as session:
        assignment = session.get(Assignment, assignment_id)
        student = session.get(Student, student_id)
        if not assignment or not student:
            return RedirectResponse(url="/?msg=Esa tarea o estudiante no existe.", status_code=303)
        course = session.get(Course, assignment.course_id)
        denied = require_course_owner(session, request, course, f"/assignments/{assignment_id}")
        if denied:
            return denied

        # La etiqueta manual solo tiene sentido para UN archivo — si se
        # suben varios a la vez, todos quedarían con la misma etiqueta, así
        # que en ese caso se ignora (igual que si se dejó vacía) y cada
        # video usa su propio nombre de archivo.
        use_manual_label = bool(label) and len(uploads) == 1

        uploaded_count = 0
        for upload in uploads:
            # Tamaño del archivo sin leerlo completo a memoria: se saca del
            # spooled temp file que ya arma Starlette al recibir el multipart.
            upload.file.seek(0, 2)
            size_bytes = upload.file.tell()
            upload.file.seek(0)

            key = f"videos/{assignment_id}/{student_id}/{uuid.uuid4().hex}.mp4"
            try:
                await run_in_threadpool(storage.upload_fileobj, upload.file, key, "video/mp4")
            except Exception:
                logger.exception("Error subiendo video a R2 (assignment=%s student=%s)", assignment_id, student_id)
                if uploaded_count:
                    session.commit()
                    return back(
                        f"Se subieron {uploaded_count} de {len(uploads)} videos — falló '{upload.filename}' "
                        "(revisa la conexión o las credenciales de R2 e intenta subir el resto de nuevo)."
                    )
                return back("No se pudo subir el video (revisa la conexión o las credenciales de R2 e intenta de nuevo).")

            video = Video(
                assignment_id=assignment_id,
                student_id=student_id,
                label=label if use_manual_label else upload.filename,
                object_key=key,
                original_filename=upload.filename,
                size_bytes=size_bytes,
            )
            session.add(video)
            uploaded_count += 1

        session.commit()

    if len(uploads) == 1:
        return back("Video subido.")
    return back(f"{uploaded_count} videos subidos.")


@app.get("/videos/{video_id}/stream")
def video_stream(video_id: int, request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    with Session(engine) as session:
        video = session.get(Video, video_id)

    if not video:
        return Response(status_code=404)
    if not storage.is_configured():
        return Response(status_code=503)

    url = storage.presigned_get_url(video.object_key)
    return RedirectResponse(url=url, status_code=307)


@app.post("/videos/{video_id}/delete")
def video_delete(video_id: int, request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    with Session(engine) as session:
        video = session.get(Video, video_id)
        if not video:
            return RedirectResponse(url="/", status_code=303)
        denied = require_course_owner(session, request, _course_for_video(session, video), f"/assignments/{video.assignment_id}")
        if denied:
            return denied
        assignment_id, student_id, key = video.assignment_id, video.student_id, video.object_key
        session.delete(video)
        session.commit()

    if storage.is_configured():
        storage.delete_object(key)

    return RedirectResponse(
        url=f"/assignments/{assignment_id}?student_id={student_id}&tab=videos&msg=Video eliminado.",
        status_code=303,
    )


@app.post("/videos/{video_id}/rename")
async def video_rename(video_id: int, request: Request):
    """Cambia solo la etiqueta (nombre visible) del video — el nombre real
    del archivo en R2 (object_key) no cambia. Existe porque al subir varios
    videos a la vez cada uno queda con el nombre del archivo como etiqueta
    (ver video_upload), y eso casi nunca es un nombre útil para el
    profesor — acá se puede corregir después, sin tener que volver a subir
    nada."""
    redirect = require_login(request)
    if redirect:
        return redirect

    form = await request.form()
    new_label = (form.get("label") or "").strip()

    with Session(engine) as session:
        video = session.get(Video, video_id)
        if not video:
            return RedirectResponse(url="/", status_code=303)
        denied = require_course_owner(session, request, _course_for_video(session, video), f"/assignments/{video.assignment_id}")
        if denied:
            return denied
        assignment_id, student_id = video.assignment_id, video.student_id
        if new_label:
            video.label = new_label
            session.add(video)
            session.commit()

    return RedirectResponse(
        url=f"/assignments/{assignment_id}?student_id={student_id}&tab=videos",
        status_code=303,
    )


@app.post("/videos/{video_id}/aspect")
async def video_set_aspect(video_id: int, request: Request):
    """Vincula (o desvincula, si llega vacío) un video a un aspecto de la
    rúbrica de su tarea — ver Video.rubric_aspect_id. Se usa desde el
    dropdown de cada video en la pestaña Videos (grading.html); el informe
    (report.html) y la pantalla de anotar (video_review.html) muestran el
    feedback/nota de ese aspecto junto al video una vez vinculado."""
    redirect = require_login(request)
    if redirect:
        return redirect

    form = await request.form()
    raw_aspect_id = (form.get("aspect_id") or "").strip()

    with Session(engine) as session:
        video = session.get(Video, video_id)
        if not video:
            return RedirectResponse(url="/", status_code=303)
        denied = require_course_owner(
            session, request, _course_for_video(session, video), f"/assignments/{video.assignment_id}"
        )
        if denied:
            return denied

        assignment_id, student_id = video.assignment_id, video.student_id
        if not raw_aspect_id:
            video.rubric_aspect_id = None
        else:
            try:
                aspect_id = int(raw_aspect_id)
            except ValueError:
                aspect_id = None
            aspect = session.get(RubricAspect, aspect_id) if aspect_id is not None else None
            # Solo se acepta un aspecto de la MISMA tarea del video — evita
            # que un id manipulado a mano vincule un aspecto de otra tarea.
            if aspect and aspect.assignment_id == video.assignment_id:
                video.rubric_aspect_id = aspect.id
        session.add(video)
        session.commit()

    # El mismo dropdown se usa desde dos pantallas (pestaña Videos y la de
    # anotar, ver video_review.html) — return_to dice a cuál volver, para no
    # sacar al docente de donde estaba solo por vincular un aspecto.
    if form.get("return_to") == "review":
        return RedirectResponse(url=f"/videos/{video_id}", status_code=303)
    return RedirectResponse(
        url=f"/assignments/{assignment_id}?student_id={student_id}&tab=videos",
        status_code=303,
    )


@app.post("/videos/{video_id}/grade")
async def video_grade_save(video_id: int, request: Request):
    """Guarda nota + feedback del aspecto vinculado a este video, desde la
    pantalla de anotar (video_review.html). Escribe la MISMA fila Grade que
    usa la pestaña Rúbrica (misma tarea+estudiante+aspecto), no una copia
    aparte — así lo que se escribe acá aparece de inmediato en Rúbrica y en
    el informe, y viceversa."""
    redirect = require_login(request)
    if redirect:
        return redirect

    form = await request.form()

    with Session(engine) as session:
        video = session.get(Video, video_id)
        if not video:
            return RedirectResponse(url="/", status_code=303)
        denied = require_course_owner(
            session, request, _course_for_video(session, video), f"/videos/{video_id}"
        )
        if denied:
            return denied

        if video.rubric_aspect_id is None:
            # No debería pasar desde la interfaz normal (el campo solo se
            # muestra con un aspecto ya vinculado), pero por si el form quedó
            # viejo en una pestaña abierta, no rompe nada, solo no hace nada.
            return RedirectResponse(url=f"/videos/{video_id}", status_code=303)

        score_raw = (form.get("score") or "").strip()
        feedback = (form.get("feedback") or "").strip()
        score = None
        if score_raw:
            try:
                score = float(score_raw)
                score = max(SCALE_MIN, min(SCALE_MAX, score))
            except ValueError:
                score = None

        existing = session.exec(
            select(Grade).where(
                Grade.assignment_id == video.assignment_id,
                Grade.student_id == video.student_id,
                Grade.aspect_id == video.rubric_aspect_id,
            )
        ).first()
        if existing:
            existing.score = score
            existing.feedback = feedback
            session.add(existing)
        else:
            session.add(
                Grade(
                    assignment_id=video.assignment_id,
                    student_id=video.student_id,
                    aspect_id=video.rubric_aspect_id,
                    score=score,
                    feedback=feedback,
                )
            )
        session.commit()

    return RedirectResponse(url=f"/videos/{video_id}", status_code=303)


# ----------------------------------------------------------- anotaciones ----
# Fase 2: dibujo libre sobre el video (color + grosor) y una nota de texto,
# guardados en un momento específico (en segundos). Todavía sin línea de
# tiempo con marcadores ni clic-para-saltar en la lista — eso es la fase 3.

# Orden fijo (no un set) porque también define el orden de los swatches en la
# barra de dibujo — el primero queda seleccionado por defecto.
ANNOTATION_COLORS = ["#5b7cfa", "#4fd6a0", "#e8b95a", "#ef7d7d", "#eef0f4"]
ALLOWED_ANNOTATION_COLORS = set(ANNOTATION_COLORS)
MAX_DRAWING_DATA_BYTES = 300_000  # generoso para trazos a mano; evita abusos


@app.get("/videos/{video_id}")
def video_review(video_id: int, request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    with Session(engine) as session:
        video = session.get(Video, video_id)
        if not video:
            return RedirectResponse(url="/?msg=Ese video no existe.", status_code=303)

        assignment = session.get(Assignment, video.assignment_id)
        student = session.get(Student, video.student_id)
        if not assignment or not student:
            return RedirectResponse(url="/?msg=Ese video no existe.", status_code=303)

        redirect_ro = require_own_assignment_view(session, request, assignment)
        if redirect_ro:
            return redirect_ro

        set_current_course(request, assignment.course_id)
        course, courses = current_course(session, request)

        aspects = session.exec(
            select(RubricAspect).where(RubricAspect.assignment_id == assignment.id).order_by(RubricAspect.order)
        ).all()
        aspects_ctx = [{"id": a.id, "name": a.name} for a in aspects]

        grade = None
        if video.rubric_aspect_id is not None:
            grade = session.exec(
                select(Grade).where(
                    Grade.assignment_id == assignment.id,
                    Grade.student_id == student.id,
                    Grade.aspect_id == video.rubric_aspect_id,
                )
            ).first()

        # Videos hermanos (misma tarea + mismo estudiante — p.ej. varios
        # intentos) para poder pasar al siguiente sin volver a la lista, tal
        # como pidió el profesor ("pasar al siguiente video sin tener que
        # devolverme a la sección de videos"). Es una navegación simple
        # (recarga de página, no un SPA como el informe): la pantalla de
        # anotar ya tiene mucho estado propio (canvas, línea de tiempo) como
        # para además manejar el cambio de video sin recargar.
        sibling_videos = session.exec(
            select(Video)
            .where(Video.assignment_id == video.assignment_id, Video.student_id == video.student_id)
            .order_by(Video.uploaded_at)
        ).all()
        sibling_ids = [v.id for v in sibling_videos]
        video_index = sibling_ids.index(video_id) if video_id in sibling_ids else 0
        prev_video_id = sibling_ids[video_index - 1] if video_index > 0 else None
        next_video_id = sibling_ids[video_index + 1] if video_index + 1 < len(sibling_ids) else None

        annotations = session.exec(
            select(Annotation).where(Annotation.video_id == video_id).order_by(Annotation.time_seconds)
        ).all()
        annotations_ctx = [
            {
                "id": a.id,
                "time_display": fmt_time_mmss(a.time_seconds),
                "is_ranged": a.end_time_seconds is not None and a.end_time_seconds > a.time_seconds,
                "end_time_display": fmt_time_mmss(a.end_time_seconds) if a.end_time_seconds is not None else None,
                "color": a.color if a.color in ALLOWED_ANNOTATION_COLORS else "#5b7cfa",
                "note": a.note,
            }
            for a in annotations
        ]
        # Para el JS: datos completos (tiempo exacto, dibujo) de cada anotación,
        # para poder saltar el video a su momento y volver a dibujar sus trazos
        # sin pedirle nada más al servidor. Se manda como JSON embebido en un
        # <script type="application/json">, no como atributos HTML, porque
        # drawing_data ya es JSON en sí mismo (habría que escapar comillas dos
        # veces). El reemplazo de "</" evita que un note con ese texto cierre
        # el <script> antes de tiempo.
        # note y stroke_width se incluyen (además de lo que ya se usaba para
        # mostrar el trazo guardado) porque el modo "editar" de una anotación
        # los precarga en el formulario sin tener que pedirlos de nuevo al
        # servidor. end_time_seconds va en null cuando la anotación es
        # puntual (comportamiento original, sin duración).
        annotations_json = json.dumps(
            [
                {
                    "id": a.id,
                    "time_seconds": a.time_seconds,
                    "end_time_seconds": a.end_time_seconds,
                    "color": a.color if a.color in ALLOWED_ANNOTATION_COLORS else "#5b7cfa",
                    "stroke_width": a.stroke_width,
                    "drawing_data": a.drawing_data,
                    "note": a.note,
                }
                for a in annotations
            ]
        ).replace("</", "<\\/")

        try:
            highlight_id = int(request.query_params.get("highlight") or 0) or None
        except ValueError:
            highlight_id = None

        # Mismo link para todos los videos de este estudiante en esta tarea
        # (no uno por video — ver get_or_create_share_token). Ojo: esta
        # función puede hacer session.commit() (si crea el token por primera
        # vez), lo que por defecto expira los atributos ya cargados de video/
        # assignment/student en esta sesión — por eso el render de la
        # plantilla (que los lee) tiene que quedar DENTRO de este `with`,
        # nunca después de que la sesión se cierre.
        token = get_or_create_share_token(session, video.assignment_id, video.student_id)
        share_url = str(request.base_url).rstrip("/") + f"/watch/{token}"

        return templates.TemplateResponse(
            "video_review.html",
            {
                "request": request,
                "video": video,
                "assignment": assignment,
                "student": student,
                "annotations": annotations_ctx,
                "annotations_json": annotations_json,
                "highlight_id": highlight_id,
                "colors": ANNOTATION_COLORS,
                "course": course,
                "courses": courses,
                "share_url": share_url,
                "can_edit": can_edit_course(request, course),
                "aspects": aspects_ctx,
                "grade": grade,
                "scale_min": SCALE_MIN,
                "scale_max": SCALE_MAX,
                "scale_min_display": str(int(SCALE_MIN)),
                "scale_max_display": str(int(SCALE_MAX)),
                "prev_video_id": prev_video_id,
                "next_video_id": next_video_id,
                "video_position": video_index + 1,
                "video_count": len(sibling_ids),
            },
        )


# Duración mínima que se acepta para considerar una anotación "con rango":
# por debajo de esto (arrastraste los dos manejadores casi al mismo punto) se
# guarda como puntual de nuevo, para no dejar barras invisibles de 0.02s. Se
# usa al crear/editar (fase 10: el rango se puede fijar arrastrando las
# manijas ANTES de guardar, ver el borrador en video_review.html) y al
# ajustar una anotación ya guardada (POST /annotations/{id}/range).
MIN_ANNOTATION_RANGE_SECONDS = 1 / 24  # un cuadro a 24fps


def _course_for_video(session: Session, video: "Video | None") -> "Course | None":
    """Sube la cadena video -> tarea -> curso para saber a quién pertenece,
    y así poder chequear el dueño antes de dejar editar/anotar/eliminar."""
    if not video:
        return None
    assignment = session.get(Assignment, video.assignment_id)
    if not assignment:
        return None
    return session.get(Course, assignment.course_id)


def _normalize_range(start: float, end: "float | None") -> "tuple[float, float | None]":
    """Ordena start/end (por si se soltó la manija de fin antes que la de
    inicio) y colapsa un rango casi nulo de vuelta a una anotación puntual
    (end=None). Compartida entre crear, editar y /range para no repetir esta
    lógica tres veces con el riesgo de que se desincronicen."""
    start = max(0.0, start)
    if end is None:
        return start, None
    end = max(0.0, end)
    if end < start:
        start, end = end, start
    return start, (end if (end - start) >= MIN_ANNOTATION_RANGE_SECONDS else None)


@app.post("/videos/{video_id}/annotations")
async def annotation_create(video_id: int, request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    with Session(engine) as session:
        video = session.get(Video, video_id)
        if not video:
            return RedirectResponse(url="/?msg=Ese video no existe.", status_code=303)
        denied = require_course_owner(session, request, _course_for_video(session, video), f"/videos/{video_id}")
        if denied:
            return denied

        form = await request.form()

        try:
            time_seconds = max(0.0, float(form.get("time_seconds") or 0))
        except ValueError:
            time_seconds = 0.0

        # end_time_seconds (fase 10): opcional -- se puede fijar arrastrando
        # las manijas del borrador ANTES de guardar (ver draft en
        # video_review.html), no solo después via POST .../range. Vacío o
        # ausente significa "sin rango" (anotación puntual), igual que antes.
        end_raw = form.get("end_time_seconds")
        try:
            end_time_seconds = float(end_raw) if end_raw not in (None, "") else None
        except ValueError:
            end_time_seconds = None
        time_seconds, end_time_seconds = _normalize_range(time_seconds, end_time_seconds)

        try:
            stroke_width = min(20.0, max(1.0, float(form.get("stroke_width") or 4)))
        except ValueError:
            stroke_width = 4.0

        color = (form.get("color") or "#5b7cfa").strip()
        if color not in ALLOWED_ANNOTATION_COLORS:
            color = "#5b7cfa"

        drawing_data = form.get("drawing_data") or "{}"
        if len(drawing_data) > MAX_DRAWING_DATA_BYTES:
            return RedirectResponse(
                url=f"/videos/{video_id}?msg=El dibujo quedó demasiado grande — prueba con menos trazos.",
                status_code=303,
            )

        note = (form.get("note") or "").strip()

        ann = Annotation(
            video_id=video_id,
            time_seconds=time_seconds,
            end_time_seconds=end_time_seconds,
            color=color,
            stroke_width=stroke_width,
            drawing_data=drawing_data,
            note=note,
        )
        session.add(ann)
        session.commit()
        session.refresh(ann)
        new_id = ann.id

    # highlight=<id>: al volver a la pantalla, salta a ese momento y vuelve a
    # dibujar los trazos que se acaban de guardar — si no, el video queda
    # pausado con el canvas en blanco y parece que el dibujo se hubiera
    # perdido (no es así: queda guardado, solo que no se mostraba de nuevo).
    return RedirectResponse(
        url=f"/videos/{video_id}?msg=Anotación guardada.&highlight={new_id}", status_code=303
    )


@app.post("/annotations/{annotation_id}/edit")
async def annotation_edit(annotation_id: int, request: Request):
    """Edita una anotación ya guardada: nota, color, grosor y el dibujo
    (se puede seguir dibujando encima de los trazos que ya tenía, o borrar
    todo y empezar de nuevo — el botón de lápiz en la lista precarga todo
    eso en el mismo formulario/canvas que se usa para crear). Desde la fase
    10 el rango (time_seconds/end_time_seconds) también se puede ajustar acá,
    arrastrando las manijas del borrador antes de guardar — antes solo se
    podía después de guardar, seleccionándola y usando POST .../range.
    """
    redirect = require_login(request)
    if redirect:
        return redirect

    with Session(engine) as session:
        ann = session.get(Annotation, annotation_id)
        if not ann:
            return RedirectResponse(url="/?msg=Esa anotación no existe.", status_code=303)
        video_id = ann.video_id
        video = session.get(Video, video_id)
        denied = require_course_owner(session, request, _course_for_video(session, video), f"/videos/{video_id}")
        if denied:
            return denied

        form = await request.form()

        try:
            time_seconds = float(form.get("time_seconds") or ann.time_seconds)
        except ValueError:
            time_seconds = ann.time_seconds

        end_raw = form.get("end_time_seconds")
        try:
            end_time_seconds = float(end_raw) if end_raw not in (None, "") else None
        except ValueError:
            end_time_seconds = ann.end_time_seconds
        ann.time_seconds, ann.end_time_seconds = _normalize_range(time_seconds, end_time_seconds)

        try:
            stroke_width = min(20.0, max(1.0, float(form.get("stroke_width") or ann.stroke_width)))
        except ValueError:
            stroke_width = ann.stroke_width

        color = (form.get("color") or ann.color).strip()
        if color not in ALLOWED_ANNOTATION_COLORS:
            color = ann.color

        drawing_data = form.get("drawing_data") or "{}"
        if len(drawing_data) > MAX_DRAWING_DATA_BYTES:
            return RedirectResponse(
                url=f"/videos/{video_id}?msg=El dibujo quedó demasiado grande — prueba con menos trazos.",
                status_code=303,
            )

        ann.color = color
        ann.stroke_width = stroke_width
        ann.drawing_data = drawing_data
        ann.note = (form.get("note") or "").strip()
        session.add(ann)
        session.commit()

    return RedirectResponse(
        url=f"/videos/{video_id}?msg=Anotación actualizada.&highlight={annotation_id}", status_code=303
    )


@app.post("/annotations/{annotation_id}/delete")
def annotation_delete(annotation_id: int, request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    with Session(engine) as session:
        ann = session.get(Annotation, annotation_id)
        if not ann:
            return RedirectResponse(url="/", status_code=303)
        video_id = ann.video_id
        video = session.get(Video, video_id)
        denied = require_course_owner(session, request, _course_for_video(session, video), f"/videos/{video_id}")
        if denied:
            return denied
        session.delete(ann)
        session.commit()

    return RedirectResponse(url=f"/videos/{video_id}?msg=Anotación eliminada.", status_code=303)


@app.post("/annotations/{annotation_id}/range")
async def annotation_set_range(annotation_id: int, request: Request):
    """Actualiza el rango (inicio/fin) de una anotación ya creada, al soltar
    uno de los manejadores de la línea de tiempo. Es la única edición de
    anotaciones que se hace por AJAX (fetch), no por un <form> normal —
    porque arrastrar un manejador no debería recargar la página ni perder el
    estado de reproducción/selección — así que responde JSON en vez de
    redirigir, y usa is_logged_in() en vez de require_login() (que
    redirigiría a /login, algo sin sentido para una llamada fetch). Sigue
    existiendo tal cual junto al ajuste de rango durante crear/editar (fase
    10): esta ruta es para el caso de arrastrar una anotación YA guardada
    (seleccionada con un clic) sin entrar al formulario de edición.
    """
    if not is_logged_in(request):
        return JSONResponse({"error": "no autenticado"}, status_code=401)

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"error": "cuerpo inválido"}, status_code=400)

    with Session(engine) as session:
        ann = session.get(Annotation, annotation_id)
        if not ann:
            return JSONResponse({"error": "esa anotación no existe"}, status_code=404)
        video = session.get(Video, ann.video_id)
        course = _course_for_video(session, video)
        if course is not None:
            if not can_edit_course(request, course):
                return JSONResponse({"error": "no puedes editar un curso de otro docente"}, status_code=403)
            if course.owner_teacher_id is None:
                teacher_id = request.session.get("teacher_id")
                if teacher_id is not None:
                    course.owner_teacher_id = teacher_id
                    session.add(course)
                    session.commit()

        try:
            start = float(body.get("start"))
            end = float(body.get("end"))
        except (TypeError, ValueError):
            return JSONResponse({"error": "start/end inválidos"}, status_code=400)

        ann.time_seconds, ann.end_time_seconds = _normalize_range(start, end)
        session.add(ann)
        session.commit()
        session.refresh(ann)

        return JSONResponse(
            {
                "id": ann.id,
                "time_seconds": ann.time_seconds,
                "end_time_seconds": ann.end_time_seconds,
                "time_display": fmt_time_mmss(ann.time_seconds),
                "end_time_display": fmt_time_mmss(ann.end_time_seconds) if ann.end_time_seconds is not None else None,
                "is_ranged": ann.end_time_seconds is not None,
            }
        )


# ------------------------------------------------------- link público -----
# El profesor comparte /watch/{share_token} con un estudiante para que vea
# sus anotaciones sin entrar al panel de notas y tareas (no pide contraseña
# — el token largo y aleatorio es lo que lo protege de que cualquiera lo
# adivine). El token es por TAREA + ESTUDIANTE (get_or_create_share_token),
# no por video individual, porque puede haber más de un video (intento) por
# estudiante en una misma tarea — el link cubre todos, con navegación
# anterior/siguiente entre ellos (?v={video_id}, validado contra ese mismo
# par tarea+estudiante). Es una versión de solo lectura de video_review: el
# mismo reproductor con línea de tiempo, marcadores y clic-para-saltar, pero
# sin la barra de dibujo, el botón de nueva anotación, ni los de editar o
# eliminar.


def _public_share_and_videos(session: Session, share_token: str):
    """Resuelve el token a su tarea+estudiante y la lista (ordenada) de sus
    videos. Devuelve (share, assignment, student, videos) o None si el token
    no existe o ya no queda nada que mostrar."""
    share = session.exec(select(VideoShare).where(VideoShare.share_token == share_token)).first()
    if not share:
        return None
    assignment = session.get(Assignment, share.assignment_id)
    student = session.get(Student, share.student_id)
    if not assignment or not student:
        return None
    videos = session.exec(
        select(Video)
        .where(Video.assignment_id == share.assignment_id, Video.student_id == share.student_id)
        .order_by(Video.uploaded_at)
    ).all()
    if not videos:
        return None
    return share, assignment, student, videos


@app.get("/watch/{share_token}")
def public_review(share_token: str, request: Request):
    with Session(engine) as session:
        resolved = _public_share_and_videos(session, share_token)
        if not resolved:
            return templates.TemplateResponse("public_not_found.html", {"request": request}, status_code=404)
        share, assignment, student, videos = resolved

        requested_id = request.query_params.get("v")
        video = None
        if requested_id:
            try:
                requested_id = int(requested_id)
            except ValueError:
                requested_id = None
            if requested_id is not None:
                video = next((v for v in videos if v.id == requested_id), None)
        if video is None:
            video = videos[0]

        idx = videos.index(video)
        prev_video = videos[idx - 1] if idx > 0 else None
        next_video = videos[idx + 1] if idx + 1 < len(videos) else None

        annotations = session.exec(
            select(Annotation).where(Annotation.video_id == video.id).order_by(Annotation.time_seconds)
        ).all()
        annotations_ctx = [
            {
                "id": a.id,
                "time_display": fmt_time_mmss(a.time_seconds),
                "is_ranged": a.end_time_seconds is not None and a.end_time_seconds > a.time_seconds,
                "end_time_display": fmt_time_mmss(a.end_time_seconds) if a.end_time_seconds is not None else None,
                "color": a.color if a.color in ALLOWED_ANNOTATION_COLORS else "#5b7cfa",
                "note": a.note,
            }
            for a in annotations
        ]
        annotations_json = json.dumps(
            [
                {
                    "id": a.id,
                    "time_seconds": a.time_seconds,
                    "end_time_seconds": a.end_time_seconds,
                    "color": a.color if a.color in ALLOWED_ANNOTATION_COLORS else "#5b7cfa",
                    "drawing_data": a.drawing_data,
                }
                for a in annotations
            ]
        ).replace("</", "<\\/")

    return templates.TemplateResponse(
        "public_review.html",
        {
            "request": request,
            "video": video,
            "assignment": assignment,
            "student": student,
            "annotations": annotations_ctx,
            "annotations_json": annotations_json,
            "share_token": share_token,
            "video_index": idx + 1,
            "video_count": len(videos),
            "prev_video_id": prev_video.id if prev_video else None,
            "next_video_id": next_video.id if next_video else None,
        },
    )


@app.get("/watch/{share_token}/stream")
def public_stream(share_token: str, request: Request):
    with Session(engine) as session:
        resolved = _public_share_and_videos(session, share_token)
        if not resolved:
            return Response(status_code=404)
        _, _, _, videos = resolved

        requested_id = request.query_params.get("v")
        video = None
        if requested_id:
            try:
                video = next((v for v in videos if v.id == int(requested_id)), None)
            except ValueError:
                video = None
        if video is None:
            video = videos[0]

    if not storage.is_configured():
        return Response(status_code=503)

    url = storage.presigned_get_url(video.object_key)
    return RedirectResponse(url=url, status_code=307)
