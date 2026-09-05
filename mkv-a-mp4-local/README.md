# Convertir .mkv a .mp4 (local, con Docker Desktop)

Herramienta chica que corre 100% en tu computador (no toca Railway ni
gasta nada de ese plan) para convertir videos `.mkv` a `.mp4`. No hay que
abrir ninguna página ni hacer clic en "convertir": arrastras el archivo a
una carpeta y en unos segundos (o minutos, si el video es largo) aparece
el `.mp4` listo en otra carpeta.

## Requisitos

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
  instalado y corriendo (el mismo que usas para la app de corrección de
  videos).

## Cómo levantarla

Desde esta carpeta (`mkv-a-mp4-local`), en una terminal:

```
docker compose up -d --build
```

Eso construye la imagen (con `ffmpeg` adentro) y deja el contenedor
corriendo en segundo plano, vigilando la carpeta `entrada/`. Con
`restart: unless-stopped` en el `docker-compose.yml`, si reinicias el
computador Docker Desktop lo vuelve a levantar solo (mientras Docker
Desktop esté corriendo).

## Cómo usarla

1. Arrastra uno o varios archivos `.mkv` a la carpeta `entrada/`.
2. Espera un poco. Si el video ya trae un códec compatible con `.mp4`
   (lo más común), la conversión es casi instantánea porque no hace falta
   recodificar, solo cambiar el "contenedor" del archivo. Si no, recodifica
   de verdad, lo que puede tardar minutos según el largo y la resolución
   del clip.
3. El `.mp4` convertido aparece en `salida/`, listo para subir a la app.
4. El `.mkv` original se mueve a `convertidos/` (no se borra) — así no se
   vuelve a procesar si reinicias el contenedor, y siempre tienes el
   original a mano por si acaso.

Si algo sale mal (archivo corrupto, formato raro que ni recodificando se
puede leer), el archivo queda en `errores/` junto con un `.txt` que
explica el motivo, en vez de quedar reintentando algo roto para siempre.

## Ver qué está pasando

```
docker compose logs -f
```

Muestra en vivo qué archivo se está convirtiendo, si fue por copia rápida
o recodificación completa, y cuándo termina cada uno. `Ctrl+C` para dejar
de mirar (el contenedor sigue corriendo igual).

## Detenerla

```
docker compose down
```

Los archivos en `entrada/`, `salida/`, `convertidos/` y `errores/` quedan
tal cual quedaron — no se pierde nada al detener el contenedor.

## Ajustar la calidad/velocidad de la recodificación

Solo aplica cuando hace falta recodificar de verdad (no al caso rápido de
copia). Se ajusta en `docker-compose.yml`, variables de entorno:

- `CRF` (por defecto `20`): más bajo = más calidad y más peso del archivo
  final. `18` se ve prácticamente idéntico al original; `23` es el valor
  por defecto normal de ffmpeg, un poco más liviano.
- `PRESET` (por defecto `medium`): qué tan a fondo comprime antes de
  escribir el archivo. De más rápido a más lento (y con mejor compresión):
  `ultrafast`, `fast`, `medium`, `slow`.

Después de cambiar algo en `docker-compose.yml`, hay que volver a levantarla:

```
docker compose up -d
```

## Por qué está separada de la app principal

Esta carpeta vive junto al resto de `VideoReview` para tenerla a mano,
pero es un contenedor Docker totalmente aparte — no se despliega a
Railway ni se mezcla con la app de corrección. Convertir un video usa
CPU de tu propio computador, no del plan de Railway.
