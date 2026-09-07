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

1. Copia `.env.example` a `.env` y cambia `TEACHER_PASSWORD` por un código
   de invitación propio (no es la contraseña de nadie para entrar día a
   día — ver "Cuentas de docente" más abajo).

   ```
   cp .env.example .env
   ```

2. Levanta la app:

   ```
   docker compose up -d
   ```

3. Abre <http://localhost:8000> en el navegador y crea tu cuenta desde
   "Crea tu cuenta de docente" en la pantalla de ingreso, usando el código
   de invitación que pusiste en `.env`.

4. Para detenerla:

   ```
   docker compose down
   ```

Tus datos (rúbrica, estudiantes, tareas, notas y feedback) quedan
guardados en la carpeta `data/` como un archivo SQLite. Mientras no borres
esa carpeta, la información persiste aunque apagues y prendas el
contenedor.

## Cuentas de docente

Cada docente tiene su propia cuenta (nombre + contraseña, creada desde
"Crea tu cuenta de docente" en el login) — así varios pueden usar la misma
app, cada uno con sus propios cursos. Para crear una cuenta hace falta el
código de invitación (`TEACHER_PASSWORD` en `.env`): pásaselo a quien
quieras invitar, no es necesario compartir tu contraseña.

Todos los docentes VEN los cursos de todos (útil para revisar entre
colegas), pero **solo el dueño de un curso puede editarlo o navegarlo** —
renombrarlo, agregar tareas o estudiantes, poner notas, subir videos,
anotar, o simplemente entrar a su pantalla de Corregir. En **Cursos** vas a
ver dos secciones: "Mis cursos" (los tuyos, editables como siempre, y los
únicos que aparecen en el selector "Curso" de la barra lateral) y "Cursos
de otros docentes" (agrupados por docente, en una lista de solo lectura).
De un curso ajeno lo único que puedes abrir es el **informe** de cada
tarea — con las notas finales y las anotaciones de video de cada
estudiante, igual que el informe de tus propios cursos — pero sin el botón
de compartir ni forma de editar nada.

Eliminar la cuenta de un docente (sus cursos quedan sin dueño, no se borra
ningún dato) es una acción restringida a una sola cuenta administradora,
configurada con `ADMIN_TEACHER_NAME` en `.env` (debe ser exactamente el
nombre con el que esa cuenta se registró). Sin esa variable, nadie ve el
botón de eliminar docente — ni siquiera tú.

Cada vez que alguien crea su cuenta, la app le arma automáticamente un
curso propio en blanco ("Mi curso"), sin estudiantes ni tareas, listo para
que le cambie el nombre y agregue sus alumnos — nadie hereda cursos ni
datos de otro docente al registrarse.

Si ya tenías cursos cargados de antes de que existieran las cuentas, no se
pierden ni se le asignan a nadie automáticamente: quedan visibles para
todos y cualquier docente logueado los puede editar hasta que alguien los
reclame (por ejemplo, renombrándolos o agregándoles algo) — desde ahí
quedan a su nombre.

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
- **Informe de curso** (`/assignments/{id}/report`): pantalla completa
  (pensada para verse en computador, no para imprimir) con las notas
  finales de todo el curso en tarjetas grandes. Al tocar el nombre de un
  estudiante, la pantalla se desliza con una transición fluida hacia su
  detalle (nota y feedback de cada aspecto de la rúbrica), y desde ahí el
  botón "Ver videos y anotaciones" abre su reproductor —con la línea de
  tiempo, el dibujo del profesor y la lista de anotaciones de siempre—
  sin salir de la página. El botón "Compartir informe" copia un link
  público (sin contraseña) con exactamente el mismo contenido y la misma
  navegación, para que el curso entero lo revise sin entrar al panel del
  profesor.
- **Videos** (pestaña dentro de Corregir): sube uno o varios videos `.mp4`
  por estudiante y tarea (por ejemplo, distintos intentos) — el selector de
  archivos permite elegir varios a la vez, así no hay que repetir el
  formulario uno por uno; también puedes arrastrar los archivos desde tu
  carpeta y soltarlos en cualquier parte de la pestaña (no solo sobre la
  tarjeta de subir) para que se suban directo, sin abrir el selector. Si
  subes varios juntos, cada video queda etiquetado con su propio nombre de
  archivo (la etiqueta manual del formulario solo aplica cuando subes uno
  solo) — el lápiz junto al nombre de cada video ya subido permite
  cambiarle el nombre después, en cualquier momento. Si alguno de los
  archivos elegidos no es un `.mp4` válido, no se sube ninguno del lote y
  se avisa cuál falló, para no dejar una subida a medias. Revísalos con los
  controles de tiempo normales del navegador. Los archivos se guardan en
  un bucket de Cloudflare R2, no en el servidor — necesita las variables
  `R2_*` configuradas (ver `.env.example`); sin ellas la pestaña se ve pero
  avisa que no puede recibir subidas todavía. Solo se aceptan `.mp4` (se
  valida la extensión y el contenido del archivo).
- **Anotar video** (botón "Anotar" en cada video): pausa el video donde
  quieras comentar, dibuja libremente encima (color y grosor a elección) y
  escribe una nota — queda guardado en ese segundo exacto del video. Al
  elegir un color/grosor o activar la goma, un pequeño círculo sigue al
  cursor (sin ocultarlo — el cursor normal del sistema sigue visible) y
  queda centrado justo en la punta, en el mismo punto exacto donde va a
  caer el trazo, mostrando de antemano el tamaño real que va a quedar (o lo
  que la goma va a alcanzar a borrar). La goma
  borra solo el tramo del trazo que toca (no el trazo completo): el dibujo
  se guarda como datos vectoriales, no como imagen, así que "borrar" corta
  el trazo justo ahí y deja el resto intacto. La lista de anotaciones de la
  derecha las muestra todas, y el lápiz de cada una permite editar su
  dibujo/nota después de creada (el papelero, eliminarla). El grosor del
  trazo se ve proporcional al tamaño de la ventana (si se achica mucho, el
  trazo se ve igual de grueso en relación al video, no fijo en píxeles). El
  reproductor es propio (no el del navegador) con dos pistas paralelas: una
  de arriba, siempre libre, para arrastrar y buscar en el video; y una de
  abajo con un marcador en forma de rombo por cada anotación (del color de
  esa anotación) — así una anotación larga nunca tapa el control para
  buscar. Al hacer clic en un marcador o en una anotación de la lista, el
  video salta directo a ese momento, vuelve a mostrar el dibujo guardado
  encima, y la anotación queda resaltada tanto en la lista como en la línea
  de tiempo. Una anotación también puede durar más de un instante: al
  crearla (o editarla, o seleccionar una ya guardada) aparecen dos manijas
  (inicio y fin) para estirarla — se convierte en una barra de color en vez
  de un rombo, y su duración se puede ajustar tanto ANTES de guardarla como
  después. Las manijas solo se ven en la anotación seleccionada o en la que
  se está creando/editando; al elegir otra desaparecen, así nunca hay dos
  pares a la vez aunque los rangos se crucen en el tiempo. Al darle play,
  cualquier anotación con duración se ve en vivo (dibujo y resaltado) apenas
  el video pasa por su tramo, sin necesidad de hacer clic — así se alcanzan
  a notar las que duran más de un cuadro.
- **Compartir con el estudiante** (botón "Compartir" en la pantalla de
  anotar, o "Compartir con [estudiante]" en la pestaña Videos): copia un
  link público al portapapeles. Es un solo link por tarea + estudiante —
  si tiene varios videos (ej. "Intento 1", "Intento 2"), el mismo link los
  cubre todos, con una navegación simple ("‹ Revisión 1 de 2 ›") para pasar
  de uno a otro. Quien lo abra ve el video y sus anotaciones (solo lectura,
  sin poder editar ni eliminar nada) sin necesitar la contraseña ni acceso
  al resto del panel de notas y tareas.

## Qué falta (fases siguientes)

- Exportación a PDF: el informe ya no tiene botón de imprimir (está
  pensado para revisarse en pantalla, no en papel). Si más adelante hace
  falta un PDF, habría que agregar un generador de verdad en el backend.

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
