"""Cliente de almacenamiento de video en Cloudflare R2.

R2 habla el mismo API que S3, así que se usa boto3 apuntando al endpoint de
R2 de tu cuenta. Todas las credenciales salen de variables de entorno —
nunca quedan escritas en el código:

- R2_ACCOUNT_ID: el ID de cuenta de Cloudflare (se ve en el dashboard de R2).
- R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY: el par de credenciales del
  "API token" de R2 que creaste (Cloudflare dashboard → R2 → Manage API
  tokens).
- R2_BUCKET_NAME: el nombre del bucket donde se guardan los videos.

Si falta alguna, `is_configured()` devuelve False y las rutas de video en
main.py muestran un aviso en vez de fallar feo.

El bucket se mantiene privado (no público): para reproducir un video, la
app genera una URL firmada (presigned) de corta duración que el navegador
usa directo contra R2 — así R2 entrega el archivo con soporte real de
"Range" (saltar a cualquier parte sin descargar todo), sin que el video
tenga que pasar por este servidor cada vez que se reproduce.
"""

import logging
import os
from functools import lru_cache
from typing import BinaryIO

logger = logging.getLogger(__name__)

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME", "")


def is_configured() -> bool:
    return bool(R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_BUCKET_NAME)


@lru_cache
def _client():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


def upload_fileobj(fileobj: BinaryIO, key: str, content_type: str = "video/mp4") -> None:
    """Sube un archivo a R2 en streaming (no carga todo el archivo a memoria).

    Es una llamada de red bloqueante (boto3 no es async) — quien la llame
    desde una ruta async debe correrla en threadpool (ver main.py).
    """
    _client().upload_fileobj(fileobj, R2_BUCKET_NAME, key, ExtraArgs={"ContentType": content_type})


def delete_object(key: str) -> None:
    """Borra un objeto de R2. No lanza si falla (ver comentario en el llamador):
    preferimos que la fila en la base de datos igual se borre aunque el borrado
    remoto falle, en vez de dejar al profesor con un botón "Eliminar" que no
    funciona por un problema de red pasajero."""
    try:
        _client().delete_object(Bucket=R2_BUCKET_NAME, Key=key)
    except Exception:
        logger.exception("No se pudo borrar %s de R2 (se ignora)", key)


def presigned_get_url(key: str, expires_in: int = 3600) -> str:
    return _client().generate_presigned_url(
        "get_object", Params={"Bucket": R2_BUCKET_NAME, "Key": key}, ExpiresIn=expires_in
    )
