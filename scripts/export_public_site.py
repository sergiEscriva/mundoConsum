"""
export_public_site.py — Genera un bundle estático público y anonimizado para GitHub Pages.

Copia únicamente:
  - index.html
  - dashboard/index.html
  - data/budgets.json (si existe; si no, estructura vacía segura)
  - data/summary.json (generado con --anonymize: sin IDs de ticket, sin direcciones ni tiendas reales)

Nunca incluye data/tickets/*.json, cookies.json, .env ni cachés internas.
"""

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from build_data import build_summary, load_all_tickets, load_budgets
from gemini_categories import GeminiError, classify_tickets, load_product_metadata

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("export_public_site")


def export_public_site(out_dir: Path, *, root: Path = ROOT) -> Path:
    root = Path(root).resolve()
    out_dir = Path(out_dir)
    if out_dir.resolve() == root:
        raise ValueError("El directorio de salida no puede ser la raíz del repositorio")

    tickets_dir = root / "data/tickets"
    tickets = load_all_tickets(tickets_dir) if tickets_dir.exists() else []

    classification = None
    if (root / "data/categories.json").exists():
        # Aplicar caché local de categorías sin exigir GEMINI_API_KEY en exportación estática
        tickets, classification = classify_tickets(tickets, root=root, strict=False, refresh=False)

    budgets = load_budgets(root / "data/budgets.json")
    metadata = load_product_metadata(root)
    summary = build_summary(
        tickets,
        budgets=budgets,
        metadata=metadata,
        anonymize=True,
    )
    if classification is not None:
        summary["classification"] = classification

    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "dashboard").mkdir(parents=True, exist_ok=True)
    (out_dir / "data").mkdir(parents=True, exist_ok=True)

    shutil.copy2(root / "index.html", out_dir / "index.html")
    shutil.copy2(root / "dashboard/index.html", out_dir / "dashboard/index.html")

    budgets_payload = budgets if budgets is not None else {"monthly": {}, "alert_monthly_total": 0}
    (out_dir / "data/budgets.json").write_text(
        json.dumps(budgets_payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (out_dir / "data/summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    log.info("Sitio público anonimizado exportado en %s (%d tickets)", out_dir, summary["total_tickets"])
    return out_dir


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Exportar dashboard estático y anonimizado para GitHub Pages")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("dist_site"),
        help="Directorio destino del bundle estático (por defecto: dist_site)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Raíz del proyecto (por defecto: raíz del repositorio)",
    )
    args = parser.parse_args(argv)
    try:
        export_public_site(args.out, root=args.root)
        return 0
    except (GeminiError, ValueError, OSError) as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
