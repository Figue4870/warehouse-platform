"""Las páginas no muestran subtítulos técnicos (fases, tecnologías, patrones)."""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGES = [ROOT / "services" / "warehouse-web" / "index.html", ROOT / "services" / "central" / "central" / "static" / "dashboard.html"]
SCRIPTS = [ROOT / "services" / "warehouse-web" / "app.js", ROOT / "services" / "central" / "central" / "static" / "dashboard.js",
           ROOT / "services" / "web-ui" / "ui.js"]
# Textos técnicos que no deben aparecer en pantalla
FORBIDDEN = ["Fase", "FastAPI", "CQRS", "(Saga", "Warehouse Cloud Platform ·", "consistencia eventual",
             "modelo de lectura", "asíncronos", "report-worker", "API + PostgreSQL", "RabbitMQ + workers"]


class VisibleText(HTMLParser):
    """Texto visible de una página (sin <script> ni <style>) y los atributos title y placeholder."""

    def __init__(self):
        super().__init__()
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        self.parts += [value for name, value in attrs if name in ("title", "placeholder") and value]

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def visible_text(path: Path) -> str:
    parser = VisibleText()
    parser.feed(path.read_text(encoding="utf-8"))
    return " ".join(parser.parts)


def js_strings(text: str) -> list[str]:
    """Cadenas de texto de un archivo JavaScript, sin contar los comentarios."""
    text = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", text, flags=re.S))
    return re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"|`([^`]*)`", text)


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_las_paginas_no_tienen_subtitulos_tecnicos(page):
    text = visible_text(page)
    assert [word for word in FORBIDDEN if word in text] == []
    assert 'class="subtitle"' not in page.read_text(encoding="utf-8")
    assert 'class="sub"' not in page.read_text(encoding="utf-8")


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_los_scripts_de_las_paginas_tampoco_los_muestran(page):
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", page.read_text(encoding="utf-8"), flags=re.S)
    shown = " ".join("".join(groups) for script in scripts for groups in js_strings(script))
    assert [word for word in FORBIDDEN if word in shown] == []


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_los_mensajes_del_frontend_no_mencionan_fases(script):
    shown = " ".join("".join(groups) for groups in js_strings(script.read_text(encoding="utf-8")))
    assert [word for word in FORBIDDEN if word in shown] == []


def test_lo_util_para_la_demo_se_mantiene():
    warehouse = SCRIPTS[0].read_text(encoding="utf-8")
    for text in ("Registrar movimiento", "(HIT)", "(MISS)", "Reportar desfase", "Producto nuevo", "Catálogo", "Transferencias"):
        assert text in warehouse, text
    central = SCRIPTS[1].read_text(encoding="utf-8")
    for text in ("Plataforma Central", "Nueva transferencia", "Reportes con Athena", "Registrar bodega", "Crear credenciales"):
        assert text in central, text


def test_las_vistas_dependen_del_rol():
    warehouse = SCRIPTS[0].read_text(encoding="utf-8")
    central = SCRIPTS[1].read_text(encoding="utf-8")
    assert "REGISTRADOR: [" in warehouse and "ADMIN_BODEGA: [" in warehouse
    assert "ADMIN_GLOBAL: [" in central and "LIDER: [" in central
