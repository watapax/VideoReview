FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV DATA_DIR=/app/data
# A propósito no se declara volumen acá en el Dockerfile: Railway rechaza el
# build si aparece esa instrucción (pide usar sus propios volúmenes en vez de
# eso). Igual queda montado en /app/data — local vía docker-compose.yml (bind
# mount), y en Railway vía el volumen que agregas desde su dashboard.

EXPOSE 8000
# Forma "shell" (sin corchetes) a propósito: así se expande la variable de entorno
# PORT en tiempo de arranque. Railway asigna su propio PORT y espera que la app
# escuche ahí; en tu Docker local (docker-compose) no se define PORT, así que
# usa 8000 por defecto y todo sigue funcionando igual que antes.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
