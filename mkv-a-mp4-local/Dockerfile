FROM python:3.12-slim

# ffmpeg trae de fábrica soporte para libx264/aac, que es lo único que usa
# watcher.py -- no hace falta compilarlo aparte ni agregar repos extra.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY watcher.py .

# Sin requirements.txt a propósito: watcher.py solo usa la librería
# estándar de Python (subprocess para llamar a ffmpeg) -- así la imagen
# se construye rápido y no depende de nada más que se pueda romper.
CMD ["python", "-u", "watcher.py"]
