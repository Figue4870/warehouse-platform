"""Tablero web de la central: una página que usa la misma API, con su sistema de diseño en /ui."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["Dashboard"])

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
PAGE = STATIC_DIR / "dashboard.html"


def ui_dir() -> Path:
    """En la imagen de Docker el sistema de diseño se copia a static/ui; en el repositorio está en services/web-ui."""
    bundled = STATIC_DIR / "ui"
    return bundled if bundled.is_dir() else STATIC_DIR.parents[2] / "web-ui"


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    return HTMLResponse(PAGE.read_text(encoding="utf-8"))
