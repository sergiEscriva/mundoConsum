"""
parse_pdf.py — Extrae y estructura los datos de los PDFs de tickets de Consum.

Formato del ticket (ejemplo real):
  2 MOZZAR.RALL.CONSUM 1,65 3,30
  1 PIZZA RIST.BABACOA 3,35
  1 A.FONTVELLA 1,5 P-6 4,32
"""

import json
import re
import sys
from pathlib import Path
from datetime import datetime

try:
    import pdfplumber
except ImportError:
    print("❌ pdfplumber no instalado. Ejecuta: pip install pdfplumber")
    sys.exit(1)

PDF_DIR = Path("data/tickets")


# ─────────────────────────────────────────────
#  CATEGORÍAS DE PRODUCTOS
#  Añade más palabras clave según tus compras
# ─────────────────────────────────────────────
CATEGORIES = {
    "Lácteos y Huevos": [
        "MOZZAR", "QUESO", "LECHE", "YOGUR", "MANTEQUIL",
        "NATA", "HUEVO", "KEFIR", "BATIDO"
    ],
    "Bebidas": [
        "FONTVELLA", "AGUA", "ZUMO", "REFRESC", "CERVEZA",
        "VINO", "COCA", "FANTA", "NESTEA", "SCHWEPPES", "HORCHATA"
    ],
    "Frutas y Verduras": [
        "MANZANA", "NARANJA", "PLATANO", "TOMATE", "LECHUGA",
        "ZANAHORIA", "CEBOLLA", "AJO", "PIMIENTO", "PEPINO",
        "LIMON", "MELON", "SANDIA", "UVA", "FRESA", "KIWI",
        "BROCOLI", "COLIFLOR", "ESPINACA", "ACELGA", "PUERRO"
    ],
    "Carne y Pescado": [
        "POLLO", "CERDO", "TERNERA", "SALMON", "ATUN",
        "JAMON", "CHORIZO", "BACON", "LONCHA", "FILETE",
        "PECHUGA", "MUSLO", "COSTILLA", "CARNE", "PESCADO",
        "MERLUZA", "DORADA", "LUBINA", "GAMBA", "MEJILLON"
    ],
    "Pan y Bollería": [
        "PAN", "BARRA", "BAGUET", "BOLLO", "CROISSANT",
        "GALLETA", "BIZCOCHO", "TOSTADA", "CEREALES", "AVENA"
    ],
    "Congelados y Preparados": [
        "PIZZA", "LASAÑA", "CANELONES", "NUGGET", "CROQUETA",
        "EMPANADA", "CONGELAD", "PRECOCID", "LISTO"
    ],
    "Pasta, Arroz y Legumbres": [
        "PASTA", "ESPAGUETI", "MACARRON", "ARROZ", "LENTEJA",
        "GARBANZO", "ALUBIA", "JUDÍA", "QUINOA"
    ],
    "Conservas y Salsas": [
        "TOMATE FRITO", "KETCHUP", "MAYONESA", "MOSTAZA",
        "ACEITE", "VINAGRE", "SALSA", "CONSERVA", "LATA"
    ],
    "Snacks y Dulces": [
        "PATATAS FRITAS", "CHIPS", "CHOCOLATE", "CARAMELO",
        "CHICLE", "FRUTOS SECOS", "ALMENDRA", "PIPAS", "PALOMITAS"
    ],
    "Higiene y Limpieza": [
        "CHAMPU", "GEL", "JABON", "COLONIA", "DESODORANTE",
        "PAPEL", "SERVILLETA", "BAYETA", "FREGASUEL", "VAJILLA",
        "LAVAVAJIL", "LAVADORA", "SUAVIZANTE", "AMBIENTADOR",
        "CEPILLO", "PASTA DENT", "COMPRESAS", "PAÑAL"
    ],
}


def categorize(product_name: str) -> str:
    """Asigna una categoría a un producto según su nombre."""
    name_upper = product_name.upper()
    for category, keywords in CATEGORIES.items():
        for kw in keywords:
            if kw in name_upper:
                return category
    return "Otros"


def parse_price(text: str) -> float:
    """Convierte '3,30' o '3.30' a float."""
    return float(text.replace(",", "."))


def extract_ticket_data(pdf_path: Path) -> dict | None:
    """Extrae todos los datos de un PDF de ticket de Consum."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            full_text = ""
            for page in pdf.pages:
                full_text += (page.extract_text() or "") + "\n"
    except Exception as e:
        print(f"  ❌ Error leyendo {pdf_path.name}: {e}")
        return None

    lines = [l.strip() for l in full_text.split("\n") if l.strip()]

    ticket = {
        "id": pdf_path.stem,
        "store": None,
        "date": None,
        "datetime_iso": None,
        "items": [],
        "total": None,
        "payment_method": None,
        "tax_breakdown": [],
        "raw_text": full_text,
    }

    # ── Fecha y hora ──────────────────────────────────────────
    # Formato: "C:0055 02/000032 24.03.2026 20:53 924421"
    date_match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})", full_text)
    if date_match:
        day, month, year, hour, minute = date_match.groups()
        try:
            dt = datetime(int(year), int(month), int(day), int(hour), int(minute))
            ticket["date"] = dt.strftime("%Y-%m-%d")
            ticket["datetime_iso"] = dt.isoformat()
        except ValueError:
            pass

    # ── Tienda ────────────────────────────────────────────────
    # Buscar línea con "V.Arquitecto" o similar
    store_match = re.search(r"(\d+\s*-\s*V\.\w[\w\s\.]+)", full_text)
    if store_match:
        ticket["store"] = store_match.group(1).strip()

    # ── Artículos ─────────────────────────────────────────────
    # Buscamos el bloque entre la cabecera y "Total factura"
    items_block_match = re.search(
        r"(?:KG\s+ARTICULO\s+€/KG\s+TOTAL|ARTICULO.*?TOTAL)\s*\n(.*?)(?:Total factura|TOTAL FACTURA)",
        full_text,
        re.DOTALL | re.IGNORECASE,
    )

    items_block = items_block_match.group(1) if items_block_match else full_text

    # Patrón de artículo:
    # "2 MOZZAR.RALL.CONSUM 1,65 3,30"  → qty=2, name=MOZZAR.RALL.CONSUM, unit_price=1.65, total=3.30
    # "1 PIZZA RIST.BABACOA 3,35"        → qty=1, name=PIZZA RIST.BABACOA, total=3.35
    item_pattern = re.compile(
        r"^(\d+(?:,\d+)?)\s+"       # cantidad
        r"([A-ZÁÉÍÓÚÜÑ][A-ZÁÉÍÓÚÜÑ0-9\.\-\/\s]+?)\s+"  # nombre
        r"(?:(\d+[,\.]\d+)\s+)?"    # precio unitario (opcional, para kg)
        r"(\d+[,\.]\d+)\s*$",        # total
        re.MULTILINE
    )

    for m in item_pattern.finditer(items_block):
        qty_str, name, unit_price_str, total_str = m.groups()
        # Ignorar líneas que son claramente cabeceras o basura
        if any(skip in name.upper() for skip in ["ARTICULO", "FACTURA", "IMPORTE", "CAMBIO", "TOTAL"]):
            continue
        try:
            qty = parse_price(qty_str)
            total = parse_price(total_str)
            unit_price = parse_price(unit_price_str) if unit_price_str else round(total / qty, 4)
            item = {
                "name": name.strip(),
                "quantity": qty,
                "unit_price": unit_price,
                "total": total,
                "category": categorize(name),
                "sold_by_kg": unit_price_str is not None,
            }
            ticket["items"].append(item)
        except (ValueError, ZeroDivisionError):
            continue

    # ── Total ─────────────────────────────────────────────────
    total_match = re.search(r"Total factura[:\s]+(\d+[,\.]\d+)", full_text, re.IGNORECASE)
    if total_match:
        ticket["total"] = parse_price(total_match.group(1))

    # ── Método de pago ────────────────────────────────────────
    if "Tarj" in full_text:
        ticket["payment_method"] = "Tarjeta"
    elif "Efectivo" in full_text or "EFECTIVO" in full_text:
        ticket["payment_method"] = "Efectivo"
    else:
        ticket["payment_method"] = "Otro"

    # ── Desglose IVA ─────────────────────────────────────────
    # Formato: "6,98 10,00 0,69 7,67"
    tax_pattern = re.compile(
        r"(\d+[,\.]\d+)\s+(\d+[,\.]\d+)\s+(\d+[,\.]\d+)\s+(\d+[,\.]\d+)"
    )
    # Solo en el bloque de factura simplificada
    tax_block = re.search(r"FACTURA SIMPLIFICADA(.*?)(?:\n\n|\Z)", full_text, re.DOTALL)
    if tax_block:
        for m in tax_pattern.finditer(tax_block.group(1)):
            base, iva_pct, cuota, importe = m.groups()
            ticket["tax_breakdown"].append({
                "base": parse_price(base),
                "iva_pct": parse_price(iva_pct),
                "cuota": parse_price(cuota),
                "importe": parse_price(importe),
            })

    return ticket


def process_all_pdfs():
    """Procesa todos los PDFs en data/tickets/ que no tienen JSON todavía."""
    pdf_files = list(PDF_DIR.glob("*.pdf"))
    if not pdf_files:
        print("ℹ️  No hay PDFs que procesar en data/tickets/")
        return 0

    processed = 0
    for pdf_path in pdf_files:
        json_path = pdf_path.with_suffix(".json")
        if json_path.exists():
            print(f"  ⏭️  Ya parseado: {pdf_path.name}")
            continue

        print(f"  📄 Parseando: {pdf_path.name} ...")
        data = extract_ticket_data(pdf_path)
        if data:
            json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            items_count = len(data["items"])
            total = data["total"] or "?"
            print(f"     ✅ {items_count} artículos, total: {total}€")
            processed += 1
        else:
            print(f"     ❌ No se pudo parsear")

    print(f"\n📊 {processed} tickets procesados")
    return processed


if __name__ == "__main__":
    # También permite parsear un PDF concreto pasado como argumento
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        if not path.exists():
            print(f"❌ No existe: {path}")
            sys.exit(1)
        result = extract_ticket_data(path)
        if result:
            print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        process_all_pdfs()
