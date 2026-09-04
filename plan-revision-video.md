# Plan de trabajo — Revisión y anotación de video

## Qué vas a poder hacer

Dentro de la pantalla de **Corregir** (donde ya calificas con la rúbrica), vas a tener una pestaña **Videos** junto a **Rúbrica**, por cada estudiante y tarea:

- Subir uno o varios videos mp4 (por ejemplo, distintos intentos de la misma entrega).
- Abrir un video y verlo con controles de tiempo (reproducir/pausar, avanzar y retroceder de a un paso corto, y arrastrar en la línea de tiempo).
- En cualquier momento del video, dibujar libremente encima (línea de acción, pose, lo que necesites), eligiendo color y grosor de brocha.
- Escribir una nota de texto para esa anotación.
- Ver un listado de todas las anotaciones del video, ordenadas por tiempo. Al hacer clic en una, el video salta directo a ese momento, muestra el dibujo encima, y esa anotación queda destacada en la lista.

## Preview visual

Publiqué un preview con dos pantallas (antes de programar nada, tal como pediste):

1. **Corregir → pestaña Videos**: la lista de videos subidos para un estudiante/tarea, con miniatura, duración y cantidad de anotaciones, más el botón para subir uno nuevo.
2. **Revisión de video**: el reproductor con controles de tiempo, la línea de tiempo con marcadores de color por anotación, la barra de herramientas de dibujo (colores + grosor), y el listado de anotaciones a la derecha con la actual destacada. Tiene un control (arriba del artboard, en el editor) para alternar entre el estado "explorando anotaciones" y "dibujando una nueva", para que veas ambos momentos del flujo.

## Decisiones que ya tomamos (según tus respuestas)

- Un video se asocia a **Tarea + Estudiante** (igual que la corrección con rúbrica), y **pueden existir varios videos** para el mismo par tarea+estudiante (ej. "Intento 1", "Intento 2").
- La revisión de video vive **dentro de Corregir**, no como una sección aparte del menú.
- Las anotaciones se guardan por **tiempo (segundos), no por número de frame** — así no dependemos de que sepas el fps de cada video. El paso "adelante/atrás" avanza en un intervalo corto y fijo (por ejemplo 0,1s), útil para afinar la posición sin necesitar saber a qué frame corresponde exactamente.

## Cómo se va a construir (bajo el capó)

**Modelo de datos nuevo:**
- `Video`: id, assignment_id, student_id, nombre del archivo, etiqueta (ej. "Intento 1"), duración, fecha de subida.
- `Annotation`: id, video_id, momento en segundos (con decimales, ej. 6,0), color, grosor de trazo, el dibujo en sí (como datos vectoriales — una lista de trazos, no una imagen — para que se vea nítido a cualquier tamaño de pantalla), texto de la nota, fecha.

**Los videos se guardan como archivos** en la misma carpeta de datos donde ya vive tu base de datos (el mismo volumen de Docker que ya tienes, sin infraestructura nueva). Vale la pena tener en cuenta que los mp4 pesan harto más que el resto de tus datos — con el uso de un semestre esto puede sumar varios GB en tu disco. No es un problema para partir, pero es algo que conviene vigilar con el tiempo (más detalle abajo, respondiendo tu pregunta sobre Google Drive).

## Tu pregunta: ¿puedo alojar los videos en Google Drive con link público?

En corto: **no te lo recomiendo para esta función específica**, aunque sí es una opción razonable para otras cosas. La razón técnica:

- El link "compartir" normal de Drive (`drive.google.com/file/d/.../view`) no es un archivo de video directo — es una página web. No sirve para que el reproductor de la app lo cargue.
- Existe un truco de link "directo" no oficial, pero Google no lo soporta como servicio de streaming: para archivos grandes (la mayoría de tus clips) muestra una pantalla intermedia de "no se puede escanear por virus" que rompe la reproducción, y aplica cuotas diarias de descarga por archivo — con varios estudiantes revisando sus videos, puede bloquearse temporalmente ("demasiados usuarios han visto este archivo"). Además puede dejar de funcionar en cualquier momento porque no es un uso oficial.
- El único link que Google sí soporta para incrustar (`.../preview`, el reproductor embebido de Drive) no te sirve para esta función porque no puedes controlarlo por código: la app necesita mandarle al reproductor "ve exactamente al segundo 6,0" para saltar a una anotación, y dibujar el trazo exactamente sincronizado con la posición del video. El reproductor embebido de Drive no permite eso — es una caja cerrada.

**Qué te recomiendo en su lugar:**
- Lo más simple: seguir guardando los videos en el mismo volumen de Docker (el plan original). Para el volumen de un profesor con un par de cursos, incluso con hartos clips cortos por semestre, estamos hablando de unos pocos GB — no debería ser un problema real en un disco de PC actual. Si en algún momento el disco se pone justo, se pueden archivar/mover a mano los videos de semestres anteriores a un disco externo sin tocar la app.
- Si en el futuro de verdad quieres sacar los videos del disco donde corre Docker (por ejemplo, si migras a un servidor con poco disco), la alternativa técnica correcta no es Google Drive sino un servicio de almacenamiento de archivos pensado para esto — por ejemplo Cloudflare R2 o Backblaze B2 — que sí entrega los videos con soporte real para "saltar" a cualquier parte sin descargar todo, y tienen un nivel gratuito razonable. Es más trabajo de configurar que Drive, así que lo dejaría para cuando realmente haga falta, no ahora.

## Fases de implementación

Para poder probar cada parte antes de entregarte la siguiente (como hemos hecho hasta ahora), lo dividiría así:

1. **Base**: modelo de datos, subida de video(s) por tarea+estudiante, pestaña "Videos" en Corregir, reproductor con controles de tiempo (sin dibujo todavía).
2. **Dibujo y anotaciones**: overlay de dibujo libre sobre el video, selector de color y grosor, guardar una anotación (dibujo + nota) en un momento específico del video.
3. **Listado y navegación**: panel de anotaciones, clic para saltar al momento con el dibujo y la anotación destacada, marcadores de color en la línea de tiempo.
4. **Pulido**: editar/eliminar anotaciones, manejo de varios videos por tarea+estudiante (pestañas o selector), pruebas de migración igual que las anteriores.

## Estado actual

**Fase 1 (Base) — lista.** Ya puedes subir videos `.mp4` desde la pestaña
**Videos** de Corregir (junto a **Rúbrica**) y verlos con los controles de
tiempo normales del navegador. Detalles de esta fase:

- Solo se aceptan `.mp4` — se valida tanto la extensión como el contenido
  del archivo (para pillar, por ejemplo, un `.mkv` renombrado a mano).
- Los archivos se guardan en tu bucket de Cloudflare R2 (variables
  `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
  `R2_BUCKET_NAME` — ver `.env.example` y `DEPLOY_RAILWAY.md`). El bucket
  queda privado; la app genera una URL firmada de corta duración cada vez
  que se reproduce un video, para que el navegador pueda saltar a
  cualquier parte sin pasar por el servidor.
- Puedes subir varios videos por tarea+estudiante (con una etiqueta como
  "Intento 1") y eliminar los que ya no sirvan.
- Todavía **no** hay dibujo ni anotaciones — eso es la fase 2.

**Fase 2 (Dibujo y anotaciones) — lista.** Desde cada video (botón
**Anotar** en la pestaña Videos) se abre la pantalla de revisión:

- Pausa el video donde quieras comentar y toca **+ Nueva anotación**.
- Dibuja libremente sobre el video (mouse, trackpad o lápiz/touch en
  tablet) con 5 colores y 3 grosores de trazo, con deshacer y borrar todo.
- Escribe una nota de texto para ese momento (puedes dejar solo dibujo,
  solo nota, o ambos).
- Guarda — la anotación queda ligada al segundo exacto del video.
- La lista de anotaciones a la derecha muestra todas las guardadas
  (ordenadas por tiempo) con su color, tiempo y nota, y puedes eliminarlas.

Probado con Playwright controlando un navegador real: el canvas de dibujo
queda perfectamente alineado con la imagen del video (sin importar la
proporción del clip), los trazos se guardan y se recuperan bien, y los
estados "explorando" / "dibujando" no se pisan entre sí.

**Fase 3 (Listado y navegación) — lista.** Debajo del video hay una línea
de tiempo propia (no la barra nativa del navegador, que no se puede
decorar) con un marcador en forma de rombo por cada anotación, del mismo
color que esa anotación, en la posición proporcional a su momento exacto.

- Al hacer clic en un marcador de la línea de tiempo, o en una anotación de
  la lista de la derecha, el video salta directo a ese segundo y vuelve a
  mostrar el trazo guardado encima — el dibujo nunca se pierde al guardar,
  solo no se volvía a mostrar hasta ahora; después de guardar una
  anotación nueva, la página salta automáticamente a ella y muestra su
  dibujo, para que quede claro que sí quedó guardado.
- Esa anotación queda resaltada a la vez en la lista (fondo y borde de
  color) y en la línea de tiempo (el rombo se agranda).
- Si mueves el video a mano (arrastrando la línea de tiempo propia o los
  controles nativos, o dándole play), el dibujo mostrado se limpia solo —
  vuelve a aparecer al hacer clic en otra anotación.

Probado igual que la fase 2, con Playwright controlando un navegador real:
los marcadores quedan en la posición correcta, el salto y el resaltado
funcionan tanto desde la lista como desde la línea de tiempo, el trazo
guardado se redibuja bien, y el flujo completo de crear→dibujar→guardar
termina mostrando el dibujo (no un canvas en blanco).

**Fase 4 (Pulido) — en curso.** Ya lista:

- **Editar una anotación guardada**: el lápiz junto a cada anotación de la
  lista abre el mismo editor de "nueva anotación" pero precargado con su
  trazo, color, grosor y nota — se puede seguir dibujando encima, borrar y
  volver a empezar, o solo cambiar el texto. El momento (segundo) de la
  anotación no se mueve al editarla.
- **Reproductor propio con una sola línea de tiempo**: antes convivían la
  barra nativa del navegador y la línea de tiempo con marcadores, lo que
  confundía (dos controles para lo mismo) y la propia no se arrastraba de
  forma fluida (solo saltaba al soltar el clic). Ahora el `<video>` no
  tiene controles nativos — hay un reproductor propio (play/pausa, tiempo,
  silencio, pantalla completa) con una sola línea de tiempo que se arrastra
  con eventos de puntero (no un solo clic), así el tiempo se actualiza en
  cada instante del arrastre.
- **Link público para el estudiante**: un token largo y aleatorio por
  **tarea + estudiante** (no por video individual); `/watch/{token}` es una
  versión de solo lectura de la pantalla de anotar (mismo reproductor, sin
  editar/crear/eliminar) que no pide contraseña — así el estudiante ve sus
  correcciones sin entrar al panel de notas y tareas. El botón "Compartir"
  (en la pantalla de anotar) y "Compartir con [estudiante]" (en la pestaña
  Videos) lo copian al portapapeles; es el mismo link sin importar desde
  qué video de ese estudiante lo copies.

Probado con Playwright: una sola línea de tiempo (sin controles nativos
dobles), el arrastre actualiza el tiempo del video en cada paso (no solo al
soltar), el lápiz precarga trazo/nota/color y guarda los cambios, el botón
compartir copia el link correcto, y la página pública funciona (salto +
trazo, sin ningún control de edición) desde un navegador sin sesión
iniciada.

**Fase 4 (Pulido) — completa.** Se agregó lo que quedaba pendiente:

- **Un solo link por varios videos**: si un estudiante tiene más de un
  video en la misma tarea (ej. "Intento 1", "Intento 2"), el botón
  "Compartir" genera **un único link** que los cubre todos — ya no uno por
  video. La página pública muestra una navegación simple arriba
  ("‹ Revisión 1 de 2 ›") para pasar al video anterior/siguiente del mismo
  estudiante, cada uno con sus propias anotaciones.
- **Arrastre de la línea de tiempo corregido**: el video se guarda en
  Cloudflare R2 y se reproduce pidiendo el archivo por tramos (HTTP Range)
  directo al navegador — cada salto (seek) implica un viaje de red. Antes,
  el arrastre pedía un salto en cada micro-movimiento del mouse, y esos
  pedidos se pisaban entre sí sin que ninguno llegara a completarse: el
  video se quedaba pegado en el mismo cuadro (a veces saltando de a un
  cuadro) en vez de mostrar el avance en tiempo real. Ahora el círculo y la
  marca de tiempo se mueven de inmediato con el mouse, pero el salto real
  del video se hace como máximo cada 150 milisegundos durante el arrastre
  (y uno final preciso al soltar) — así el video sí avanza visiblemente
  cuadro a cuadro mientras arrastras, incluso con la latencia real de la
  red. Se probó simulando esa latencia (120ms por pedido) con Playwright, y
  se confirmó que el tiempo del video avanza de forma continua durante un
  arrastre lento y aterriza exacto donde se suelta.

Probado con Playwright: el link es idéntico visto desde cualquiera de los
videos del mismo estudiante/tarea, la página pública arranca en el primer
video con el contador de navegación correcto, "siguiente"/"anterior" carga
el video y las anotaciones correctas de cada uno, y el arrastre de la línea
de tiempo avanza en tiempo real bajo latencia de red simulada.

**Fase 5 (Informe público + goma) — lista.**

- **Goma en la barra de dibujo**: junto a deshacer/borrar todo, un botón de
  goma — al activarla, tocar o arrastrar sobre un trazo lo borra. No es un
  borrador de píxeles (el dibujo se guarda como datos vectoriales, no como
  imagen — ver `Annotation.drawing_data`), así que borra a nivel de
  **trazo completo**: cualquier trazo que la goma toque desaparece entero,
  los demás quedan intactos. Se desactiva sola al cambiar de color/grosor o
  al salir del modo dibujo.
- **El informe de curso también es un link público**: el botón "Compartir
  informe" (junto a "Exportar / Imprimir") copia un link (`/report/{token}`,
  un token por tarea) con el mismo informe, sin pedir contraseña — para que
  el curso completo lo revise sin entrar al panel del profesor. En
  "Resumen de notas finales", cada estudiante que tenga al menos un video
  subido en esa tarea muestra un botón "Videos" directo a sus anotaciones
  (mismo link que genera "Compartir" en la pantalla de anotar).
- **Rediseño del informe**: dejó el estilo claro/vintage (fondo crema,
  tipografía de imprenta) por uno oscuro y colorido a tono con el resto de
  la app (mismos colores y tipografía Manrope), con textos más grandes y
  cada aspecto de la rúbrica con su propio color para ubicarse más fácil.
  Al imprimir/exportar a PDF vuelve a un estilo claro de alto contraste
  (pensado para papel, no para pantalla).

Probado con Playwright: la goma borra un trazo sin afectar los demás y el
resultado se guarda así; el botón "Compartir informe" copia el link
correcto; la página pública del informe muestra el mismo contenido sin los
controles del profesor (sin botón compartir, sin volver a Corregir); el
botón de video en el resumen de notas solo aparece para estudiantes con
videos subidos; y un token de informe inválido muestra la página amigable
de "link no válido".

## Qué falta

Nada pendiente de esta función por ahora — las cinco fases están
completas y probadas.
