# Corrección de Animación

App web para calificar trabajos de animación con una rúbrica de aspectos
ponderados, dejar feedback por aspecto, y exportar un informe de curso
agrupado por aspecto (para que los estudiantes aprendan del feedback de
sus compañeros).

Reemplaza la planilla Excel que se usaba antes. Corre en Docker para que
sea fácil moverla más adelante a un servidor/VPS real sin rehacer nada.

## Requisitos

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) instalado y corriendo.

## Cómo correrla en tu computador

1. Copia `.env.example` a `.env` y cambia `TEACHER_PASSWORD` por una
   contraseña propia (es la única que se pide para entrar a la app).

   ```
   cp .env.example .env
   ```

2. Levanta la app:

   ```
   docker compose up -d
   ```

3. Abre <http://localhost:8000> en el navegador y entra con la contraseña
   que pusiste en `.env`.

4. Para detenerla:

   ```
   docker compose down
   ```

Tus datos (rúbrica, estudiantes, tareas, notas y feedback) quedan
guardados en la carpeta `data/` como un archivo SQLite. Mientras no borres
esa carpeta, la información persiste aunque apagues y prendas el
contenedor.

## Qué incluye por ahora

- **Cursos** (`/courses`): cada curso tiene su propia lista de estudiantes y
  sus propias tareas — completamente separados. El selector de curso está
  arriba en la barra lateral; cambia el curso activo para toda la
  navegación.
- **Rúbrica** (por tarea, desde `/assignments/{id}/rubric`): cada tarea
  tiene su propia rúbrica y ponderaciones (deben sumar 100%) — dos tareas
  del mismo curso pueden evaluar aspectos distintos. Una tarea nueva parte
  copiando la rúbrica de la tarea más reciente del curso, o con una rúbrica
  de ejemplo si es la primera tarea.
- **Estudiantes** (`/students`): agrega estudiantes uno por uno o pegando
  una lista completa, al curso activo.
- **Tareas** (`/`): crea una tarea por cada entrega del semestre, dentro del
  curso activo.
- **Corregir** (`/assignments/{id}`): pantalla de corrección — elige un
  estudiante, pon nota y feedback en cada aspecto, la nota final se
  calcula sola.
- **Informe de curso** (`/assignments/{id}/report`): informe agrupado por
  aspecto con la nota y el feedback de todo el curso, listo para exportar
  a PDF con el botón "Exportar / Imprimir" del navegador (Ctrl/Cmd+P).
- **Videos** (pestaña dentro de Corregir): sube uno o varios videos `.mp4`
  por estudiante y tarea (por ejemplo, distintos intentos), y revísalos con
  los controles de tiempo normales del navegador. Los archivos se guardan
  en un bucket de Cloudflare R2, no en el servidor — necesita las variables
  `R2_*` configuradas (ver `.env.example`); sin ellas la pestaña se ve pero
  avisa que no puede recibir subidas todavía. Solo se aceptan `.mp4` (se
  valida la extensión y el contenido del archivo).
- **Anotar video** (botón "Anotar" en cada video): pausa el video donde
  quieras comentar, dibuja libremente encima (color y grosor a elección) y
  escribe una nota — queda guardado en ese segundo exacto del video. La
  lista de anotaciones de la derecha las muestra todas, y el lápiz de cada
  una permite editar su dibujo/nota después de creada (el papelero,
  eliminarla). El reproductor es propio (no el del navegador) con una sola
  línea de tiempo — sin barras dobles — que se puede arrastrar de forma
  fluida y que muestra un marcador en forma de rombo por cada anotación
  (del color de esa anotación). Al hacer clic en un marcador o en una
  anotación de la lista, el video salta directo a ese momento, vuelve a
  mostrar el dibujo guardado encima, y la anotación queda resaltada tanto
  en la lista como en la línea de tiempo.
- **Compartir con el estudiante** (botón "Compartir" en la pantalla de
  anotar, o "Copiar link para el estudiante" en cada video de la pestaña
  Videos): copia un link público al portapapeles. Quien lo abra ve el video
  y las anotaciones de ese video (solo lectura, sin poder editar ni
  eliminar nada) sin necesitar la contraseña ni acceso al resto del panel
  de notas y tareas.

## Qué falta (fases siguientes)

- Manejo más pulido de cuando hay varios videos por tarea+estudiante.
- Exportación a PDF "de verdad" (por ahora es imprimir la página del
  informe desde el navegador, que ya se ve bien pero no es un PDF generado
  por el backend).

## Cuando quieras moverla a un servidor (más adelante)

Esta app está pensada para migrar tal cual, sin reescribir nada. Para
Railway específicamente (el plan actual), ver **[DEPLOY_RAILWAY.md](DEPLOY_RAILWAY.md)**
con los pasos completos.

Para cualquier otro proveedor que corra Docker (Hetzner, DigitalOcean, un
VPS propio):

1. Copia toda esta carpeta al servidor (o clónala si la subes a un repo
   git).
2. Crea el archivo `.env` ahí con una contraseña y una `SECRET_KEY`
   distintas a las de tu computador.
3. Corre `docker compose up -d` en el servidor. Eso es todo — mismo
   comando, mismo contenedor.
4. Si quieres que la app tenga un dominio propio (en vez de
   `servidor:8000`), se agrega un proxy como Caddy o Nginx delante — te
   ayudo a dejarlo configurado cuando llegues a ese paso.

No hay nada en el código atado a Docker en tu computador ni a ningún
proveedor en particular — el mismo `docker compose up -d` funciona igual
en tu máquina o en cualquier otro lugar que corra Docker.
