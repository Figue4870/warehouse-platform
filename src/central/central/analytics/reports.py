"""Consultas analíticas compartidas por la API y Terraform."""
import re
from pathlib import Path

REPORTS = {
    "ventas_por_dia": "Ventas por día y bodega",
    "top_productos": "Productos más vendidos",
    "stock_actual": "Última foto de existencias",
    "movimientos_por_tipo": "Movimientos por tipo y bodega",
    "transferencias": "Resultados y duración de transferencias",
}


def render_sql(name: str, database: str, days: int) -> str:
    if name not in REPORTS:
        raise KeyError(name)
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9]*", database) or not 1 <= days <= 365:
        raise ValueError("Base de Athena o rango de días inválido")
    template = (Path(__file__).parent / "sql" / f"{name}.sql").read_text(encoding="utf-8")
    return template.replace("${database}", database).replace("${days}", str(days))
