import os

from fastapi import Request
from starlette.responses import RedirectResponse

TEACHER_PASSWORD = os.environ.get("TEACHER_PASSWORD", "changeme")


def is_logged_in(request: Request) -> bool:
    return bool(request.session.get("logged_in"))


def require_login(request: Request):
    """Devuelve un RedirectResponse a /login si no hay sesión, o None si está OK.

    Se usa al principio de cada ruta protegida:
        redirect = require_login(request)
        if redirect:
            return redirect
    """
    if not is_logged_in(request):
        return RedirectResponse(url="/login", status_code=303)
    return None
