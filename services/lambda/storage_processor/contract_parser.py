"""Extrae la metadata de un contrato a partir de su texto (formato de la plantilla de contratos).

Campos: proveedor, NIT, fecha de firma, vigencia y precios pactados por SKU.
"""

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

DATE = r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4})"
LABELS = {
    "supplier_name": re.compile(r"^\s*proveedor\s*[:\-]\s*(.+?)\s*$", re.I),
    "supplier_nit": re.compile(r"\bnit\b\s*(?:no\.?|n\.?)?\s*[:\-]?\s*([0-9][0-9.\s]{4,18}[0-9](?:\s*-\s*[0-9])?)", re.I),
    "signed_on": re.compile(r"fecha\s+de\s+firma\s*[:\-]\s*" + DATE, re.I),
    "valid_until": re.compile(r"(?:vigente|vigencia)\s+hasta\s*[:\-]\s*" + DATE, re.I),
}
PRICES_HEADER = re.compile(r"precios?\s+pactados?", re.I)
PRICE_LINE = re.compile(r"^\s*([A-Z0-9][A-Z0-9._-]{2,63})\s*[:|\-]?\s*\$?\s*([0-9][0-9.,]*)\s*$")


def _plain(text: str) -> str:
    """Sin tildes, para que 'Vigencia' y 'Vigéncia' o 'Número' coincidan igual."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def parse_date(value: str) -> Optional[date]:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(value: str) -> Optional[Decimal]:
    """Acepta 17.500,00 / 17,500.00 / 17500.00 / 17.500 (en pesos el punto suele ser de miles)."""
    value = value.strip().rstrip(".,")
    if "." in value and "," in value:
        decimal_sep = "," if value.rfind(",") > value.rfind(".") else "."
        thousands = "." if decimal_sep == "," else ","
        value = value.replace(thousands, "").replace(decimal_sep, ".")
    elif "," in value:
        head, _, tail = value.rpartition(",")
        value = value.replace(",", "") if len(tail) == 3 else head.replace(",", "") + "." + tail
    elif value.count(".") > 1 or ("." in value and len(value.rpartition(".")[2]) == 3):
        value = value.replace(".", "")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return None
    return amount.quantize(Decimal("0.01")) if amount > 0 else None


def parse_contract(text: str) -> dict:
    fields: dict = {}
    prices: list[dict] = []
    in_prices = False
    for raw_line in text.splitlines():
        line = _plain(raw_line).strip()
        if not line:
            continue
        for name, pattern in LABELS.items():
            if name in fields:
                continue
            match = pattern.search(line)
            if not match:
                continue
            value = match.group(1).strip()
            if name in ("signed_on", "valid_until"):
                parsed = parse_date(value)
                if parsed:
                    fields[name] = parsed.isoformat()
            elif name == "supplier_nit":
                fields[name] = re.sub(r"[\s.]", "", value)
            else:
                fields[name] = raw_line.split(":", 1)[-1].strip() if ":" in raw_line else value
        if PRICES_HEADER.search(line):
            in_prices = True
            continue
        if in_prices:
            match = PRICE_LINE.match(line)
            if match:
                amount = parse_amount(match.group(2))
                if amount is not None:
                    prices.append({"sku": match.group(1), "price": f"{amount:.2f}"})
            elif prices:
                in_prices = False
    if prices:
        seen = {}
        for item in prices:
            seen[item["sku"]] = item  # si un SKU aparece dos veces, vale el último
        fields["agreed_prices"] = list(seen.values())
    return fields
