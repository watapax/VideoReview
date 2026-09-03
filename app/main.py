import logging
import os
import uuid
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse, Response

from app import storage
from app.auth import TEACHER_PASSWORD, require_login
from app.database import engine, init_db
from app.models import Annotation, Assignment, Course, Grade, RubricAspect, Student, Video
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

APP_DIR = os.path.dirname(__file__)

app = FastAPI(title="Corrección de Animación")
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "dev-secret-change-me"))
app.mount("/static", StaticFiles(directory=os.path.join(APP_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(APP_DIR, "templates"))


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    with Session(engine) as session:
        seed_defaults(session)


def initials(name: str) -> str:
    parts = [p for p in name.strip().split() if p]
    letters = "".join(p[0].upper() for p in parts[:2])
    return letters or "?"


def current_course(session: Session, request: Request) -> tuple[Course, list[Course]]:
    """Curso activo de esta sesión + la lista de cursos activos para el selector.

    Si la sesión no tiene un curso elegido (o el guardado ya no existe/está
    inactivo), elige el primero disponible y lo deja guardado.
    """
    courses = session.exec(select(Course).where(Course.active == True).order_by(Course.name)).all()  # noqa: E712
    if not courses:
        course = Course(name="Curso 1", active=True)
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


# ---------------------------------------------------------------- auth ----

@app.get("/login")
def login_form(request: Request):
    if request.session.get("logged_in"):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    password = form.get("password", "")
    if password == TEACHER_PASSWORD:
        request.session["logged_in"] = True
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": "Contraseña incorrecta."}, status_code=401
    )


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
    redirect = require_login(request)
    if redirect:
        return redirect
    with Session(engine) as session:
        course, courses = current_course(session, request)
        all_courses = session.exec(select(Course).order_by(Course.name)).all()
        counts = []
        for c in all_courses:
            n_students = len(
                session.exec(
                    select(Student).where(Student.course_id == c.id, Student.active == True)  # noqa: E712
                ).all()
            )
            n_assignments = len(session.exec(select(Assignment).where(Assignment.course_id == c.id)).all())
            counts.append({"course": c, "n_students": n_students, "n_assignments": n_assignments})
    return templates.TemplateResponse(
        "courses.html",
        {"request": request, "active": "courses", "course": course, "courses": courses, "rows": counts},
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
            c = Course(name=name, active=True)
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


@app.get("/courses/switch")
def courses_switch(request: Request, course_id: int):
    redirect = require_login(request)
    if redirect:
        return redirect
    with Session(engine) as session:
        c = session.get(Course, course_id)
        if c and c.active:
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
    rows = list(zip(ids, names, weights))

    with Session(engine) as session:
        assignment = session.get(Assignment, assignment_id)
        if not assignment:
            return RedirectResponse(url="/?msg=Esa tarea no existe.", status_code=303)

        set_current_course(request, assignment.course_id)
        course, courses = current_course(session, request)

        def rerender(error: str):
            submitted = [{"id": rid, "name": rname, "weight": rweight} for rid, rname, rweight in rows]
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
            total = sum(float(w) for _id, _name, w in rows)
        except ValueError:
            return rerender("Alguna ponderación no es un número válido.")

        if not rows:
            return rerender("Necesitas al menos un aspecto en la rúbrica.")
        if abs(total - 100) > 0.5:
            return rerender(f"Las ponderaciones deben sumar 100% (ahora suman {total:g}%).")

        existing = session.exec(
            select(RubricAspect).where(RubricAspect.assignment_id == assignment.id)
        ).all()
        submitted_ids = {int(rid) for rid, _n, _w in rows if rid != "new"}
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

        for idx, (rid, rname, rweight) in enumerate(rows):
            rname = rname.strip()
            rweight_f = float(rweight)
            if rid == "new":
                session.add(RubricAspect(name=rname, weight=rweight_f, order=idx, assignment_id=assignment.id))
            else:
                obj = session.get(RubricAspect, int(rid))
                if obj and obj.assignment_id == assignment.id:
                    obj.name = rname
                    obj.weight = rweight_f
                    obj.order = idx
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
        {"request": request, "active": "students", "students": students, "course": course, "courses": courses},
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
        if s:
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
        if current:
            videos = session.exec(
                select(Video)
                .where(Video.assignment_id == assignment_id, Video.student_id == current["id"])
                .order_by(Video.uploaded_at)
            ).all()
            for v in videos:
                ann_count = len(session.exec(select(Annotation).where(Annotation.video_id == v.id)).all())
                videos_ctx.append(
                    {
                        "id": v.id,
                        "label": v.label or v.original_filename or "Video",
                        "size_display": fmt_size(v.size_bytes),
                        "uploaded_display": fmt_date_es(v.uploaded_at),
                        "annotation_count": ann_count,
                    }
                )

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
            "active_tab": active_tab,
            "r2_configured": storage.is_configured(),
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

@app.get("/assignments/{assignment_id}/report")
def report(assignment_id: int, request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    with Session(engine) as session:
        assignment = session.get(Assignment, assignment_id)
        if not assignment:
            return RedirectResponse(url="/?msg=Esa tarea no existe.", status_code=303)

        set_current_course(request, assignment.course_id)
        course, _courses = current_course(session, request)

        aspects = session.exec(
            select(RubricAspect).where(RubricAspect.assignment_id == assignment.id).order_by(RubricAspect.order)
        ).all()
        students = session.exec(
            select(Student)
            .where(Student.course_id == assignment.course_id, Student.active == True)  # noqa: E712
            .order_by(Student.name)
        ).all()
        grades_all = session.exec(select(Grade).where(Grade.assignment_id == assignment_id)).all()

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

        summary = []
        for s in ordered_students:
            grade = finals[s.id]
            pct = 0.0
            if grade is not None:
                pct = max(0.0, min(100.0, (grade - SCALE_MIN) / (SCALE_MAX - SCALE_MIN) * 100))
            summary.append({"name": s.name, "grade_display": fmt(grade), "pct": round(pct, 1)})

        aspects_ctx = []
        for a in aspects:
            rows = []
            for s in ordered_students:
                g = grades_by_student.get(s.id, {}).get(a.id)
                score = g.score if g else None
                rows.append(
                    {
                        "name": s.name,
                        "score_display": fmt(score) if score is not None else "—",
                        "band": band(score),
                        "feedback": g.feedback if g else "",
                    }
                )
            aspects_ctx.append(
                {"name": a.name, "weight_display": weight_display(a.weight), "rows": rows}
            )

    return templates.TemplateResponse(
        "report.html",
        {
            "request": request,
            "assignment": assignment,
            "course": course,
            "summary": summary,
            "aspects": aspects_ctx,
            "scale_min_display": str(int(SCALE_MIN)),
            "scale_max_display": str(int(SCALE_MAX)),
            "today": datetime.now().strftime("%d-%m-%Y"),
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
    upload = form.get("file")
    label = (form.get("label") or "").strip()

    if upload is None or not getattr(upload, "filename", ""):
        return back("Elige un archivo de video.")

    header = await upload.read(12)
    await upload.seek(0)

    if not _is_valid_mp4(upload.filename, header):
        return back("Solo se aceptan videos en formato .mp4 (ese archivo no parece ser un mp4 válido).")

    with Session(engine) as session:
        assignment = session.get(Assignment, assignment_id)
        student = session.get(Student, student_id)
        if not assignment or not student:
            return RedirectResponse(url="/?msg=Esa tarea o estudiante no existe.", status_code=303)

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
            return back("No se pudo subir el video (revisa la conexión o las credenciales de R2 e intenta de nuevo).")

        video = Video(
            assignment_id=assignment_id,
            student_id=student_id,
            label=label or upload.filename,
            object_key=key,
            original_filename=upload.filename,
            size_bytes=size_bytes,
        )
        session.add(video)
        session.commit()

    return back("Video subido.")


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
        assignment_id, student_id, key = video.assignment_id, video.student_id, video.object_key
        session.delete(video)
        session.commit()

    if storage.is_configured():
        storage.delete_object(key)

    return RedirectResponse(
        url=f"/assignments/{assignment_id}?student_id={student_id}&tab=videos&msg=Video eliminado.",
        status_code=303,
    )


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

        set_current_course(request, assignment.course_id)
        course, courses = current_course(session, request)

        annotations = session.exec(
            select(Annotation).where(Annotation.video_id == video_id).order_by(Annotation.time_seconds)
        ).all()
        annotations_ctx = [
            {
                "id": a.id,
                "time_display": fmt_time_mmss(a.time_seconds),
                "color": a.color if a.color in ALLOWED_ANNOTATION_COLORS else "#5b7cfa",
                "note": a.note,
            }
            for a in annotations
        ]

    return templates.TemplateResponse(
        "video_review.html",
        {
            "request": request,
            "video": video,
            "assignment": assignment,
            "student": student,
            "annotations": annotations_ctx,
            "colors": ANNOTATION_COLORS,
            "course": course,
            "courses": courses,
        },
    )


@app.post("/videos/{video_id}/annotations")
async def annotation_create(video_id: int, request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    with Session(engine) as session:
        video = session.get(Video, video_id)
        if not video:
            return RedirectResponse(url="/?msg=Ese video no existe.", status_code=303)

        form = await request.form()

        try:
            time_seconds = max(0.0, float(form.get("time_seconds") or 0))
        except ValueError:
            time_seconds = 0.0

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

        session.add(
            Annotation(
                video_id=video_id,
                time_seconds=time_seconds,
                color=color,
                stroke_width=stroke_width,
                drawing_data=drawing_data,
                note=note,
            )
        )
        session.commit()

    return RedirectResponse(url=f"/videos/{video_id}?msg=Anotación guardada.", status_code=303)


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
        session.delete(ann)
        session.commit()

    return RedirectResponse(url=f"/videos/{video_id}?msg=Anotación eliminada.", status_code=303)
