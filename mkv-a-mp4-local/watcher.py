"""Vigila la carpeta 'entrada' y convierte cada .mkv que aparezca a .mp4 en
'salida', sin necesidad de abrir nada -- solo arrastrar el archivo a la
carpeta. Corre en un loop simple (sin dependencias extra más que ffmpeg,
ya instalado en la imagen) porque es más robusto que inotify/watchdog
sobre carpetas montadas desde Windows con Docker Desktop.

Flujo por archivo:
  1. Espera a que el tamaño del archivo deje de cambiar (Windows a veces
     tarda unos segundos en terminar de copiar un archivo grande a la
     carpeta -- si se empieza a convertir a medias, sale corrupto).
  2. Intenta un "remux" (copiar los streams de video/audio tal cual, solo
     cambiar el contenedor de .mkv a .mp4) -- es casi instantáneo y no usa
     CPU, funciona cuando el mkv ya trae video H.264/H.265 y audio
     AAC/MP3/AC3 (lo más común al exportar desde editores o capturas).
  3. Si el remux falla (códec no compatible con mp4), recodifica de
     verdad con libx264 -- más lento, usa CPU real de TU computador (esto
     corre 100% local, no toca Railway ni gasta nada de ese plan).
  4. Si ambos intentos fallan, deja el archivo en 'errores' junto con un
     .txt con el motivo, para no quedar reintentando algo roto para
     siempre.
  5. El .mkv original se mueve a 'convertidos' (no se borra) para no
     volver a procesarlo si se reinicia el contenedor.
"""
import os
import shutil
import subprocess
import time
from pathlib import Path

DATA = Path("/data")
ENTRADA = DATA / "entrada"
SALIDA = DATA / "salida"
CONVERTIDOS = DATA / "convertidos"
ERRORES = DATA / "errores"

POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "2"))
CRF = os.environ.get("CRF", "20")
PRESET = os.environ.get("PRESET", "medium")

for d in (ENTRADA, SALIDA, CONVERTIDOS, ERRORES):
    d.mkdir(parents=True, exist_ok=True)


def log(msg):
    print(f"[mkv-a-mp4] {msg}", flush=True)


def is_stable(path: Path, wait: float = 1.5) -> bool:
    """True si el tamaño del archivo no cambió en `wait` segundos --
    señal de que ya terminó de copiarse a la carpeta (y no sigue a medias)."""
    try:
        size1 = path.stat().st_size
    except FileNotFoundError:
        return False
    time.sleep(wait)
    try:
        size2 = path.stat().st_size
    except FileNotFoundError:
        return False
    return size1 == size2 and size1 > 0


def unique_path(folder: Path, name: str) -> Path:
    """Evita pisar un archivo existente agregando _1, _2... antes de la
    extensión si ya hay algo con ese nombre."""
    candidate = folder / name
    if not candidate.exists():
        return candidate
    stem, suffix = Path(name).stem, Path(name).suffix
    i = 1
    while True:
        candidate = folder / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def run_ffmpeg(args):
    return subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args,
        capture_output=True, text=True,
    )


def convert(src: Path):
    out_path = unique_path(SALIDA, src.stem + ".mp4")
    # OJO: el nombre temporal tiene que seguir terminando en ".mp4" -- si
    # se le agrega un sufijo tipo ".part" al final, ffmpeg ya no reconoce
    # la extensión y falla al elegir el formato de salida. Por eso el
    # "temporal" es un archivo oculto (con punto adelante) al lado del
    # definitivo, no una extensión rara.
    tmp_out = SALIDA / f".tmp_{out_path.name}"

    log(f"convirtiendo: {src.name}")

    # 1) Remux -- copia los streams de video/audio tal cual, sin
    #    recodificar (rápido, casi sin uso de CPU). Se descartan
    #    subtítulos/adjuntos con -map (algunos formatos de subtítulo de
    #    .mkv no son válidos dentro de un .mp4 y harían fallar el remux
    #    por una razón que no tiene que ver con el video/audio).
    result = run_ffmpeg([
        "-i", str(src),
        "-map", "0:v:0?", "-map", "0:a:0?",
        "-c", "copy", "-movflags", "+faststart",
        str(tmp_out),
    ])
    mode = "remux (sin recodificar)"

    if result.returncode != 0:
        # 2) El contenedor mp4 no acepta ese códec tal cual -- recodifica
        #    de verdad. Esto sí usa CPU real y puede tardar minutos según
        #    el largo/resolución del clip.
        tmp_out.unlink(missing_ok=True)
        mode = f"recodificado (preset={PRESET}, crf={CRF})"
        result = run_ffmpeg([
            "-i", str(src),
            "-map", "0:v:0?", "-map", "0:a:0?",
            "-c:v", "libx264", "-preset", PRESET, "-crf", CRF,
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(tmp_out),
        ])

    if result.returncode != 0:
        tmp_out.unlink(missing_ok=True)
        err_path = unique_path(ERRORES, src.name)
        shutil.move(str(src), str(err_path))
        (ERRORES / (err_path.stem + "_error.txt")).write_text(
            f"No se pudo convertir {src.name}.\n\nSalida de ffmpeg:\n{result.stderr}"
        )
        log(f"FALLO: {src.name} -> revisa errores/{err_path.stem}_error.txt")
        return

    tmp_out.rename(out_path)
    done_path = unique_path(CONVERTIDOS, src.name)
    shutil.move(str(src), str(done_path))
    log(f"listo ({mode}): {src.name} -> salida/{out_path.name}")


def main():
    log("vigilando la carpeta 'entrada' -- arrastra tus .mkv ahi")
    while True:
        for f in sorted(ENTRADA.glob("*")):
            if not f.is_file():
                continue
            if f.suffix.lower() != ".mkv":
                continue
            if f.name.startswith("."):
                continue
            if is_stable(f):
                convert(f)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
