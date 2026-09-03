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

## Siguiente paso

Si el preview te acomoda, parto con la Fase 1. Si quieres ajustar algo del diseño (colores de anotación, cómo se ve la lista, dónde queda el botón de subir) o de las decisiones de arriba, lo vemos antes de programar.
