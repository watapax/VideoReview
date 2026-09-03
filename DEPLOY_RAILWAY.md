# Desplegar en Railway

Esta guía la puedes seguir cuando estés de vuelta en tu computador — necesitas
la carpeta del proyecto a mano y una terminal. Ya dejé el proyecto listo para
esto (ver "Qué preparé" al final); lo único que falta es que tú ejecutes estos
pasos una vez, porque necesitan tu login de Railway.

## 0. Antes de empezar: ¿qué pasa con los datos que ya tienes?

Tu base de datos local (`data/correccion.db`, con tus cursos, estudiantes,
tareas y notas reales) **no se sube sola a Railway** — Railway parte con una
base de datos vacía. Tienes dos caminos:

- **Empezar limpio en Railway** y seguir usando la versión local en paralelo
  hasta que decidas el corte definitivo.
- **Migrar tus datos reales**: cuando llegues al paso 5 (volumen), en vez de
  dejar que la app cree una base de datos nueva, subes tu `correccion.db`
  actual al volumen antes de la primera vez que arranca. Te ayudo con el
  paso exacto cuando estés ahí — depende de si conectas por CLI o dashboard.

No hagas nada con tus datos todavía; solo ten claro cuál de los dos caminos
prefieres antes de llegar al paso 5.

## 1. Instala la CLI de Railway

Como ya tienes Docker Desktop con WSL2 habilitado, lo más simple es instalar
la CLI dentro de WSL (abre "Ubuntu" o tu distro de WSL desde el menú inicio):

```
curl -fsSL agents.railway.com | sh
```

(Si prefieres no usar WSL, la alternativa es `npm i -g @railway/cli` si
tienes Node instalado.)

## 2. Inicia sesión

```
railway login
```

Esto abre el navegador para que confirmes con tu cuenta.

## 3. Conecta la carpeta del proyecto

Desde dentro de la carpeta `correccion-app`:

```
railway init
```

Te va a pedir un nombre para el proyecto (ej. "correccion-animacion") y
crearlo. Railway detecta el `Dockerfile` automáticamente — no hace falta
configurar nada de build.

## 4. Prueba gratis antes de pagar

Railway da 30 días de prueba con US$5 de crédito, sin pedir tarjeta. Alcanza
de sobra para dejar esto funcionando y probarlo con calma antes de decidir el
plan pago (Hobby, US$5/mes).

## 5. Agrega un volumen persistente (para que las notas no se borren)

Esto se hace desde el dashboard (railway.com → tu proyecto):

1. Click derecho en el canvas del proyecto → **Volume** (o `⌘K` / `Ctrl+K` y
   busca "Volume").
2. Conéctalo a tu servicio.
3. En la configuración del volumen, pon como **mount path**: `/app/data`
   (tiene que ser exactamente esa ruta — es donde la app espera encontrar la
   base de datos, igual que en tu Docker local).

Si vas a migrar tus datos reales (ver paso 0): este es el momento de subir tu
`correccion.db` actual a esa ruta antes del primer arranque. Avísame cuando
llegues aquí y lo hacemos juntos — depende de qué herramienta uses para
conectarte al volumen.

## 6. Configura las variables de entorno

Con la CLI, desde la carpeta del proyecto:

```
railway variable set TEACHER_PASSWORD=tu-contraseña-elegida
railway variable set SECRET_KEY=algo-largo-y-aleatorio
```

Para generar un `SECRET_KEY` seguro (no reutilices el de tu `.env` local, usa
uno distinto para producción), corre esto y copia el resultado:

```
python3 -c "import secrets; print(secrets.token_hex(32))"
```

No hace falta configurar `DATA_DIR` — el `Dockerfile` ya lo deja en
`/app/data`, que es donde montaste el volumen en el paso 5.

## 7. Despliega

```
railway up
```

Esto sube el código, construye la imagen con tu `Dockerfile` y la deja
corriendo. Vas a ver los logs de arranque en la terminal (los mismos que ves
localmente con `docker compose up`).

## 8. Genera la dirección pública

Desde el dashboard: tu servicio → **Settings → Networking → Public
Networking → Generate Domain**. Te da una URL tipo
`correccion-animacion.up.railway.app` con HTTPS automático, lista para
compartir contigo mismo (nadie más necesita entrar, sigue pidiendo tu
contraseña).

## 9. Verifica

Abre la URL, entra con tu `TEACHER_PASSWORD`, y confirma que ves tu curso (o
la base de datos vacía, si elegiste partir limpio). Si migraste datos reales,
revisa que tus cursos, estudiantes y notas estén completos.

---

## Qué preparé (ya hecho, no tienes que tocarlo)

- **`Dockerfile`**: ahora el contenedor escucha en el puerto que Railway le
  asigna (`$PORT`) en vez de tener el 8000 fijo. Seguí probándolo en la
  nube y confirmé que local (`docker compose`, sin `PORT` definido seguido
  cae en 8000 como siempre) y en modo "Railway" (con `PORT` puesto por
  ejemplo en 8080) arrancan correctamente sin cambiar nada más.
- No agregué archivos de configuración de Railway (`railway.json`): Railway
  detecta el `Dockerfile` solo, y ese formato de configuración está en vías
  de reemplazo por una herramienta nueva (Infrastructure as Code, todavía en
  beta) — mejor evitarlo por ahora y configurar lo poco que falta (volumen,
  variables, dominio) desde el dashboard, como describe esta guía.

## Sobre los videos y Cloudflare R2

La función de revisión de video (con las anotaciones dibujadas) todavía no
está construida — vamos a empezar esa parte después de dejar esto
desplegado. Cuando la construya, la app va a subir/leer los videos
directamente desde tu bucket de R2 (no desde el volumen de Railway), así que
puedes seguir subiendo tus archivos a R2 con tranquilidad — cuando esa
función esté lista, solo va a necesitar que le pases las credenciales del
bucket como variables de entorno adicionales (`railway variable set`, igual
que en el paso 6). No se pierde nada de lo que subas ahora.
