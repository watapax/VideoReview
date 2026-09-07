import binascii
import hashlib
import hmac
import os

from fastapi import Request
from starlette.responses import RedirectResponse

# Código de invitación para crear una cuenta nueva en /signup — es la misma
# variable de entorno que antes era "la" contraseña única de la app (cuando
# solo existía un profesor). Ahora protege el registro, no el login: sin
# este código, cualquiera con el link podría crearse una cuenta y ver los
# cursos de los demás (ver TEACHER_PASSWORD en README/DEPLOY_RAILWAY.md).
TEACHER_PASSWORD = os.environ.get("TEACHER_PASSWORD", "changeme")

# Nombre de la cuenta "administradora" -- la del profesor dueño de la
# instalación. Solo esa cuenta puede eliminar la cuenta de OTROS docentes
# (ver /teachers/{id}/delete en main.py): antes cualquier docente logueado
# podía hacerlo, lo cual no tiene sentido cuando varias personas comparten
# la misma instalación. Se compara sin distinguir mayúsculas/minúsculas,
# igual que el login (ver name_lower en Teacher) -- debe ser exactamente el
# nombre con el que esa cuenta se registró en /signup. Si no está
# configurada, nadie es admin (la función queda deshabilitada por defecto,
# nunca abierta a cualquiera).
ADMIN_TEACHER_NAME = os.environ.get("ADMIN_TEACHER_NAME", "")

_PBKDF2_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    """Sal + hash PBKDF2-SHA256, codificados juntos en un solo string
    "sal_hex$hash_hex". Sin dependencias nuevas (bcrypt/passlib) para no
    complicar el build de Docker/Railway — para el volumen de esta app
    (un puñado de docentes) PBKDF2 con harto de iteraciones es más que
    suficiente."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"{binascii.hexlify(salt).decode()}${binascii.hexlify(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        salt = binascii.unhexlify(salt_hex)
        expected = binascii.unhexlify(digest_hex)
    except (ValueError, binascii.Error):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return hmac.compare_digest(actual, expected)


def is_logged_in(request: Request) -> bool:
    return request.session.get("teacher_id") is not None


def current_teacher_id(request: Request):
    return request.session.get("teacher_id")


def is_admin_teacher(request: Request) -> bool:
    """True solo para la cuenta cuyo nombre coincide con ADMIN_TEACHER_NAME
    (ver arriba). Se guarda "teacher_name" en la sesión al iniciar sesión o
    registrarse (ver /login, /signup en main.py), así que no hace falta
    tocar la base de datos para chequear esto en cada request."""
    if not ADMIN_TEACHER_NAME:
        return False
    name = request.session.get("teacher_name")
    return bool(name) and name.strip().lower() == ADMIN_TEACHER_NAME.strip().lower()


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
