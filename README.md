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
- [Instalación](#instalación)
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

## Instalación

```bash
# 1. Clonar el repositorio
git clone https://github.com/TU_USUARIO/consum-tracker.git
cd consum-tracker

# 2. Crear y activar un entorno virtual
python -m venv venv
source venv/bin/activate        # macOS / Linux
venv\Scripts\activate           # Windows

# 3. Instalar dependencias
pip install -r requirements.txt
playwright install chromium
```

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
| `1` | Error de autenticación (cookies expiradas o inválidas) |
| `2` | Sin tickets nuevos por procesar (no-op) |

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
