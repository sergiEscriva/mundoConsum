# Consum Tracker

[![Daily workflow](https://github.com/sergiEscriva/mundoConsum/actions/workflows/daily.yml/badge.svg)](https://github.com/sergiEscriva/mundoConsum/actions/workflows/daily.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![GitHub Pages](https://img.shields.io/badge/GitHub%20Pages-live-brightgreen?logo=github)](https://sergiescriva.github.io/mundoConsum/dashboard/)

Dashboard personal para monitorizar tus compras en Consum, con actualización automática diaria via GitHub Actions y visualización en GitHub Pages.

---

## Requisitos previos

- Cuenta en GitHub (con [GitHub Student](https://education.github.com/pack) si quieres repos privados gratis)
- Python 3.10 o superior instalado en tu ordenador
- Cuenta en [Consum Mundo Consum](https://www.consum.es/mundoconsum/)

---

## Instalación paso a paso

### Paso 1 — Crear el repositorio en GitHub

1. Ve a [github.com/new](https://github.com/new)
2. Nombre: `consum-tracker`
3. Visibilidad: **Private** (recomendado, con Student es gratis)
4. NO marques ninguna opción de inicialización (sin README, sin .gitignore)
5. Haz clic en **Create repository**

---

### Paso 2 — Subir este código al repositorio

Abre una terminal en la carpeta donde hayas descargado este proyecto y ejecuta:

```bash
git init
git add .
git commit -m "Primer commit — Consum Tracker"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/consum-tracker.git
git push -u origin main
```

> Sustituye `TU_USUARIO` por tu nombre de usuario de GitHub.

---

### Paso 3 — Instalar dependencias localmente

```bash
# Crear un entorno virtual (recomendado)
python -m venv venv

# Activarlo
# En macOS/Linux:
source venv/bin/activate
# En Windows:
venv\Scripts\activate

# Instalar dependencias
pip install -r requirements.txt
playwright install chromium
```

---

### Paso 4 — Capturar las cookies de sesión (importante)

Este es el paso clave. El script abre un navegador para que tú hagas login con tu usuario y contraseña + 2FA:

```bash
python scripts/get_cookie.py
```

**¿Qué pasará?**
1. Se abrirá una ventana de Chrome con la página de login de Consum
2. Introduces tu email, contraseña y el código 2FA (SMS o email)
3. Cuando veas tu perfil cargado, vuelves a la terminal
4. Pulsas `ENTER`
5. Se genera el archivo `cookies.json` con tu sesión

> **NUNCA subas `cookies.json` a GitHub.** Ya está en `.gitignore` pero sé cuidadoso.

---

### Paso 5 — Guardar las cookies en GitHub Secrets

1. Ve a tu repositorio en GitHub
2. Haz clic en **Settings** (pestaña superior)
3. En el menú izquierdo: **Secrets and variables > Actions**
4. Haz clic en **New repository secret**
5. Rellena:
   - **Name:** `CONSUM_COOKIES`
   - **Value:** copia y pega TODO el contenido del archivo `cookies.json`
6. Haz clic en **Add secret**

---

### Paso 6 — Activar GitHub Pages

1. En tu repositorio, ve a **Settings > Pages**
2. En **Source**, selecciona **Deploy from a branch**
3. Branch: `main` | Folder: `/ (root)`
4. Haz clic en **Save**
5. En unos minutos, tu dashboard estará en:
   `https://TU_USUARIO.github.io/consum-tracker/dashboard/`

---

### Paso 7 — Probar manualmente el primer ciclo

Para verificar que todo funciona antes de esperar al día siguiente:

1. Ve a tu repositorio en GitHub
2. Haz clic en **Actions** (pestaña superior)
3. En el menú izquierdo: **Consum Daily Tracker**
4. Haz clic en **Run workflow > Run workflow**
5. Observa los logs en tiempo real

Si todo va bien, verás todos los pasos en verde.

---

## Uso diario

El sistema funciona solo. Cada mañana a las 8:00 (hora España):

1. GitHub Actions se despierta
2. Entra a Consum con tus cookies
3. Descarga tickets nuevos y actualiza `data/tickets/*.json`
4. Regenera `data/summary.json`
5. Hace commit automático
6. GitHub Pages actualiza el dashboard

**Tú no tienes que hacer nada.** Solo abrir el dashboard cuando quieras ver tus datos.

---

## Cuando las cookies expiren (cada ~2-4 semanas)

Sabrás que han expirado porque recibirás un email de GitHub diciendo que el Action falló.

Para renovarlas:

```bash
# Activa el entorno virtual si lo tienes
source venv/bin/activate

# Ejecuta el capturador
python scripts/get_cookie.py
```

Luego actualiza el Secret en GitHub (Paso 5).

---

## Estructura del proyecto

```
mundoConsum/
├── .github/
│   ├── ISSUE_TEMPLATE/        ← Plantillas para issues
│   ├── workflows/
│   │   ├── daily.yml          ← Automatización diaria (GitHub Actions)
│   │   └── release.yml        ← Crea un GitHub Release al publicar un tag
│   ├── dependabot.yml         ← Actualizaciones automáticas de dependencias
│   └── PULL_REQUEST_TEMPLATE.md
├── scripts/
│   ├── get_cookie.py          ← Ejecutas localmente para capturar sesión
│   ├── scraper.py             ← Descarga tickets y extrae artículos
│   └── build_data.py          ← Genera summary.json para el dashboard
├── data/
│   ├── tickets/               ← Un .json por ticket procesado
│   └── summary.json           ← Datos agregados que lee el dashboard
├── dashboard/
│   └── index.html             ← El dashboard (GitHub Pages)
├── .gitignore
├── CHANGELOG.md
├── CONTRIBUTING.md
├── LICENSE
├── SECURITY.md
├── requirements.txt
└── README.md
```

---

## Solución de problemas

| Problema | Solución |
|---|---|
| El Action falla con "sesión expirada" | Regenera cookies con `get_cookie.py` y actualiza el Secret |
| No aparecen tickets en el dashboard | El scraper puede no reconocer el HTML de Consum. Abre un Issue en el repo |
| El dashboard no carga (`summary.json` no encontrado) | Asegúrate de que GitHub Pages apunta a la raíz (`/`) del branch `main` |
| `playwright install` falla en Windows | Ejecuta la terminal como Administrador |

---

## Contribuir

Las contribuciones son bienvenidas. Consulta [CONTRIBUTING.md](CONTRIBUTING.md) para las pautas.

## Licencia

Distribuido bajo la licencia [MIT](LICENSE).

## Nota legal

Este proyecto es para uso personal. Automatizar el acceso a sitios web puede ir contra sus términos de servicio. Úsalo con moderación y responsabilidad.
