# Consum Tracker

[![Daily Tracker](https://github.com/sergiEscriva/mundoConsum/actions/workflows/daily.yml/badge.svg)](https://github.com/sergiEscriva/mundoConsum/actions/workflows/daily.yml)
[![Tests](https://github.com/sergiEscriva/mundoConsum/actions/workflows/tests.yml/badge.svg)](https://github.com/sergiEscriva/mundoConsum/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![GitHub Pages](https://img.shields.io/badge/dashboard-live-brightgreen?logo=github)](https://sergiescriva.github.io/mundoConsum/dashboard/)

**Consum Tracker** es un sistema automatizado que descarga, clasifica y visualiza tus compras en [Consum](https://www.consum.es/mundoconsum/). Se ejecuta a diario mediante GitHub Actions y publica los resultados en un dashboard interactivo servido con GitHub Pages.

---

## Tabla de contenidos

- [Características](#características)
- [Arquitectura](#arquitectura)
- [Requisitos previos](#requisitos-previos)
- [Ejecutar en local](#ejecutar-en-local)
- [Configuración de secretos](#configuración-de-secretos)
- [Despliegue del dashboard](#despliegue-del-dashboard)
- [Uso](#uso)
- [Renovación de cookies](#renovación-de-cookies)
- [Presupuestos y alertas](#presupuestos-y-alertas)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Tests](#tests)
- [Solución de problemas](#solución-de-problemas)
- [Contribuir](#contribuir)
- [Licencia](#licencia)
- [Aviso legal](#aviso-legal)

---

## Características

- **Scraping automatizado** de tickets mediante Playwright y la API de `nomastickets.consum.es`.
- **Clasificación por categorías** (carne, bebidas, frutas, higiene, etc.) con reglas configurables en `data/categories.json`.
- **Dashboard interactivo** con Chart.js: gasto mensual, distribución por categoría, productos frecuentes, media móvil y comparativas temporales.
- **Presupuestos por categoría** con barras de progreso y alertas visuales cuando se excede el límite.
- **Exportación a CSV** directamente desde el dashboard.
- **Buscador de productos** integrado con filtro por categoría.
- **Tips de salud y ahorro** generados a partir de los patrones de compra.
- **Notificaciones automáticas**: issue en GitHub y notificación opcional por Telegram cuando las cookies expiran.
- **Ejecución diaria desatendida** vía GitHub Actions (cron a las 08:00 hora de España).
- **Suite de tests** con pytest para validar la lógica de negocio.

---

## Arquitectura

```
                   ┌──────────────────────────────────┐
                   │        GitHub Actions (cron)      │
                   │  daily.yml — cada día a las 08:00 │
                   └────────┬─────────────┬────────────┘
                            │             │
                   ┌────────▼──────┐  ┌───▼──────────────┐
                   │  scraper.py   │  │  build_data.py    │
                   │  (Playwright) │  │  (Agregación)     │
                   └────────┬──────┘  └───┬──────────────┘
                            │             │
                   ┌────────▼─────────────▼────────────┐
                   │         data/                      │
                   │   tickets/*.json  summary.json     │
                   └────────────────┬──────────────────┘
                                    │
                   ┌────────────────▼──────────────────┐
                   │     dashboard/index.html           │
                   │     (GitHub Pages — Chart.js)      │
                   └───────────────────────────────────┘
```

---

## Requisitos previos

| Requisito | Detalle |
|-----------|---------|
| Python | 3.10 o superior |
| Cuenta GitHub | Se recomienda visibilidad privada para el repositorio |
| Cuenta Consum | Registrada en [Mundo Consum](https://www.consum.es/mundoconsum/) con 2FA activo |

---

## Ejecutar en local

Necesitas **Python 3.10 o superior** (recomendado: 3.12). En Windows, activa la
opción «Add Python to PATH» durante su instalación.

```bash
git clone https://github.com/sergiEscriva/mundoConsum.git
cd mundoConsum
python run.py
```

En macOS/Linux, usa `python3` si no tienes el comando `python`. En Windows puedes
usar `py -3 run.py`.

El lanzador crea `.venv`, instala las dependencias, regenera los datos y abre el
dashboard en tu navegador. **No necesitas activar un entorno virtual, instalar
Chromium ni iniciar sesión para ver los datos existentes.** La primera ejecución
necesita Internet para instalar paquetes; las siguientes reutilizan el entorno.

También puedes hacer doble clic en **`iniciar.bat` en Windows** o
**`iniciar.command` en macOS**. El acceso de macOS también funciona desde una
terminal de Linux: `./iniciar.command`.

Mantén abierta la terminal mientras uses el dashboard y pulsa **Ctrl+C** para
cerrarlo. Si el puerto 8000 está ocupado, el lanzador elige otro y muestra la
dirección correcta. Funciona aunque lo invoques desde otra carpeta, usando la
ruta completa a `run.py`.

### Descargar tus compras

```bash
python run.py update
```

El lanzador prepara Chromium y, si no tienes sesión, abre el navegador para que
inicies sesión y completes el 2FA. Vuelve a la terminal y pulsa Enter cuando
termine el login. Después descarga los tickets y genera el resumen. Si la sesión
ha caducado, te guía para renovarla y reintenta una vez. Recarga el dashboard
para ver los cambios. Puedes ejecutar este comando en otra terminal mientras
el dashboard permanece abierto.

Si un detalle no puede descargarse, la actualización falla y queda disponible
para reintento. Si los detalles se descargaron pero algunas líneas no cuadran con
el total o el parser no reconoce su formato, se actualiza el dashboard **con avisos**:
el gasto usa los totales originales y las diferencias quedan visibles en
«Tickets pendientes de revisión». No se inventan descuentos ni artículos para
hacer cuadrar los números. Esos tickets se revisarán de nuevo al actualizar.
La descarga reutiliza la pestaña de la aplicación para conservar la sesión al
abrir los detalles. Si falla, el mensaje `Detalle ...: fallo en ...` indica la
fase y el error HTTP o del navegador sin mostrar el token. Tras tres detalles
consecutivos inaccesibles, se detiene en lugar de repetir el fallo en toda la lista.

### Comandos disponibles

| Comando | Para qué sirve |
|---------|----------------|
| `python run.py` | Preparar y abrir el dashboard |
| `python run.py update` | Descargar tickets y regenerar los datos |
| `python run.py login` | Iniciar sesión o renovar las cookies |
| `python run.py build` | Regenerar el resumen sin descargar tickets |
| `python run.py categorize` | Configurar Gemini y clasificar todos los productos históricos |
| `python run.py setup` | Preparar dependencias y Chromium por adelantado |
| `python run.py test` | Ejecutar las pruebas con el entorno del proyecto |
| `python run.py --port 8080 --no-browser` | Arrancar sin abrir una ventana y preferir otro puerto |

Los accesos `iniciar.bat` e `iniciar.command` también aceptan estos argumentos
desde una terminal, por ejemplo `iniciar.bat update`.

### Clasificar todos los productos con Gemini

```bash
python run.py categorize
```

La primera vez pide tu clave de [Google AI Studio](https://aistudio.google.com/apikey)
sin mostrarla y la guarda en `.env`, excluido de Git. No pegues claves en el código,
en el chat ni en archivos que vayas a compartir. Si una clave se ha compartido,
revócala y utiliza una nueva. También puedes definir `GEMINI_API_KEY` en tu entorno;
esa variable tiene prioridad sobre `.env`.

Gemini recibe únicamente nombres de productos e identificadores de lote, junto
con las categorías permitidas. No recibe cookies, tickets completos, precios,
fechas ni datos de tu cuenta Consum. Las llamadas pueden consumir la cuota o
generar cargos según tu proyecto de Google.

La clasificación incluye **todos los nombres únicos**, aunque ya tuvieran una
categoría asignada por palabras clave. Se procesa por lotes de hasta 40 productos.
Las respuestas se validan y guardan en `data/product_categories.json`; los tickets
originales permanecen intactos y el dashboard utiliza las categorías de Gemini.
La caché no contiene la clave y puede versionarse junto con los datos.

Después de configurarlo, `update`, `build` y el arranque normal consultan Gemini
solo para productos nuevos. Si la API falla, mantienen las categorías disponibles
y muestran la cobertura y los pendientes en el dashboard. El comando `categorize`
exige terminar la clasificación y muestra un error si falta cuota o hay un fallo;
los lotes ya completados quedan guardados para continuar al repetirlo.

Para volver a clasificar todo deliberadamente (consume nuevas llamadas):

```bash
python run.py categorize --refresh-categories
```

La selección predeterminada es `GEMINI_MODEL=auto`: se consulta el catálogo de
modelos accesibles con tu clave y se prioriza un Flash de texto estable. Se
excluyen variantes especializadas de imagen/audio. Si el primer modelo devuelve
404, se prueban hasta otras dos opciones del catálogo antes de fallar. Los errores
de clave o cuota se notifican sin tratarlos como errores de modelo.

Puedes fijar un modelo mediante `GEMINI_MODEL` en `.env` o en el entorno. Un modelo
fijado se respeta; usa `auto` si ese nombre ha dejado de estar disponible. La caché
guarda el modelo realmente utilizado y se reutiliza sin consultar la API si todos
los productos ya están clasificados. Al necesitar nuevas llamadas, se conserva el
modelo de la caché mientras esté disponible. Cambiar el modelo o los nombres de
las categorías invalida la caché y requiere nuevas llamadas. Las categorías permitidas se leen
de `data/categories.json`, incluyendo Hogar y Menaje, Mascotas y Otros para
productos que no se puedan identificar razonablemente.

Si usas la ejecución diaria de GitHub Actions, configura opcionalmente el secreto
`GEMINI_API_KEY` y la variable `GEMINI_MODEL` en GitHub. Sin clave, se reutiliza
la caché disponible y los productos nuevos conservan temporalmente su categoría
local, con un aviso visible.

En Debian/Ubuntu, si Python indica que falta soporte para entornos virtuales,
instala `python3-venv`. Si Chromium requiere bibliotecas del sistema, ejecuta
`python run.py setup --with-deps` (puede solicitar permisos de administrador).
Las pruebas de navegador requieren Chromium; prepáralo con `setup` si quieres
ejecutar la suite completa. Windows instala automáticamente `tzdata` para
`Europe/Madrid`.

La sesión se guarda en `cookies.json`, ignorado por Git. No necesitas GitHub
Secrets para usarlo en tu equipo. Si defines `CONSUM_COOKIES`, se usa primero;
cuando `update` renueva una sesión rechazada, usa las cookies locales nuevas
para ese reintento. En ejecuciones sin terminal interactiva, prepara antes las
cookies con `python run.py login`.

Durante el login puedes usar las pestañas de la ventana de Chromium abierta por
el programa. Las redirecciones y el 2FA siguen funcionando mientras la terminal
espera Enter. La comprobación de acceso a los tickets se hace en otra pestaña;
si todavía no se confirma, la ventana permanece abierta para terminar el proceso
y volver a pulsar Enter. Escribe `salir` para cancelar sin cambiar tus cookies.

El servidor solo escucha en tu equipo y sirve el dashboard y sus JSON; no expone
`cookies.json`, los tickets individuales ni el repositorio. Chart.js se carga
desde un CDN: si no hay conexión, los gráficos muestran un aviso y el resto de
la aplicación sigue funcionando.

Los scripts de `scripts/` siguen disponibles para automatizaciones. El lanzador
muestra los errores y devuelve un código distinto de cero cuando falla una fase.
Los datos inválidos detienen la generación sin sustituir el resumen anterior.

---

## Configuración de secretos

### Captura de cookies de sesión

El scraper necesita cookies de sesión válidas. Para obtenerlas, ejecuta el asistente interactivo:

```bash
python scripts/get_cookie.py
```

El script abrirá un navegador donde deberás:

1. Introducir tus credenciales de Consum.
2. Completar la verificación 2FA.
3. Esperar a que cargue el panel de usuario.
4. Volver a la terminal y pulsar `ENTER`.

El fichero `cookies.json` se generará en la raíz del proyecto. **Este fichero nunca debe subirse al repositorio** (ya está incluido en `.gitignore`).

### Guardar el secreto en GitHub

1. Ve a **Settings > Secrets and variables > Actions** en tu repositorio.
2. Crea un nuevo secreto con nombre `CONSUM_COOKIES`.
3. Pega el contenido íntegro de `cookies.json` como valor.

### Secretos opcionales

| Secreto | Descripción |
|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Token del bot de Telegram para recibir notificaciones |
| `TELEGRAM_CHAT_ID` | ID del chat donde enviar las notificaciones |

---

## Despliegue del dashboard

1. En tu repositorio, accede a **Settings > Pages**.
2. Selecciona **Deploy from a branch**.
3. Configura: branch `main`, carpeta `/ (root)`.
4. Guarda los cambios.

El dashboard estará disponible en:

```
https://TU_USUARIO.github.io/consum-tracker/dashboard/
```

---

## Uso

### Ejecución automática

El workflow `daily.yml` se ejecuta cada día a las 08:00 (hora de España). El proceso completo es:

1. Autenticación con las cookies almacenadas.
2. Descarga de tickets nuevos desde la API de Consum.
3. Extracción y clasificación de artículos por categoría.
4. Generación del fichero `data/summary.json`.
5. Commit automático y actualización del dashboard.

No requiere intervención manual.

### Ejecución manual

Para lanzar el workflow manualmente, ve a **Actions > Consum Daily Tracker > Run workflow**.

### Códigos de salida del scraper

| Código | Significado |
|--------|-------------|
| `0` | Tickets nuevos procesados correctamente |
| `1` | Cookies ausentes, inválidas o sesión rechazada |
| `2` | Sin tickets nuevos por procesar (no-op) |
| `3` | Error de red/API o detalles inaccesibles; revisar los logs y reintentar |
| `4` | Detalles descargados con descuadres o líneas sin reconocer; el lanzador genera el dashboard con avisos |

---

## Renovación de cookies

Las cookies de sesión expiran aproximadamente cada 2 a 4 semanas. Cuando esto ocurra:

- El workflow fallará y se creará automáticamente un **issue** en el repositorio.
- Si tienes configurado Telegram, recibirás una **notificación** en tu chat.

Para renovarlas:

```bash
source venv/bin/activate          # o venv\Scripts\activate en Windows
python scripts/get_cookie.py
```

Después, actualiza el secreto `CONSUM_COOKIES` en GitHub con el nuevo contenido de `cookies.json`.

---

## Presupuestos y alertas

Puedes definir presupuestos mensuales por categoría editando `data/budgets.json`:

```json
{
  "monthly": {
    "Carne y Pescado": 80,
    "Bebidas": 20,
    "Frutas y Verduras": 40
  },
  "alert_monthly_total": 300
}
```

- El dashboard mostrará **barras de progreso** por categoría (verde / naranja / rojo).
- Si el gasto total del mes supera `alert_monthly_total`, aparecerá una **alerta visual**.
- Las categorías de productos se configuran en `data/categories.json`.

---

## Estructura del proyecto

```
mundoConsum/
├── .github/
│   ├── ISSUE_TEMPLATE/             Plantillas para issues
│   ├── workflows/
│   │   ├── daily.yml               Automatización diaria
│   │   ├── release.yml             Release automático por tags
│   │   └── tests.yml               CI con pytest
│   ├── dependabot.yml              Actualizaciones de dependencias
│   └── PULL_REQUEST_TEMPLATE.md
├── scripts/
│   ├── get_cookie.py               Captura interactiva de cookies
│   ├── scraper.py                  Descarga y parseo de tickets
│   └── build_data.py               Generación de summary.json
├── tests/
│   ├── test_scraper.py             Tests del scraper
│   └── test_build_data.py          Tests del agregador
├── data/
│   ├── tickets/                    Un JSON por ticket procesado
│   ├── categories.json             Reglas de categorización
│   ├── budgets.json                Presupuestos por categoría
│   └── summary.json                Datos agregados para el dashboard
├── dashboard/
│   └── index.html                  Dashboard interactivo (GitHub Pages)
├── .gitignore
├── CHANGELOG.md
├── CONTRIBUTING.md
├── SECURITY.md
├── LICENSE
├── requirements.txt
└── README.md
```

---

## Tests

El proyecto incluye una suite de tests unitarios que cubren la lógica de clasificación, parseo de tickets y generación del resumen.

```bash
pytest tests/ -v
```

Los tests se ejecutan automáticamente en CI con cada push a `main` y en cada pull request.

---

## Solución de problemas

| Problema | Causa probable | Solución |
|----------|---------------|----------|
| El workflow falla con "sesión expirada" | Cookies caducadas | Ejecutar `get_cookie.py` y actualizar el secreto |
| El dashboard no carga datos | `summary.json` no existe | Verificar que GitHub Pages apunta a la raíz de `main` |
| No aparecen artículos en los tickets | Cambio en el HTML de Consum | Abrir un issue con los logs del scraper |
| `playwright install` falla en Windows | Permisos insuficientes | Ejecutar la terminal como administrador |
| Muchos productos en categoría "Otros" | Palabras clave no cubiertas | Editar `data/categories.json` y añadir las keywords |

---

## Contribuir

Las contribuciones son bienvenidas. Consulta [CONTRIBUTING.md](CONTRIBUTING.md) para conocer las pautas del proyecto.

---

## Licencia

Distribuido bajo la licencia [MIT](LICENSE). Copyright (c) 2026 Sergi Escriva Siscar.

---

## Aviso legal

Este proyecto es de uso estrictamente personal. La automatización del acceso a sitios web de terceros puede contravenir sus condiciones de servicio. El autor no se responsabiliza del uso que se haga de este software. Utilízalo con moderación y bajo tu propia responsabilidad.
