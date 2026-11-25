"""Genera un contrato de proveedor en PDF con el formato que la Lambda sabe leer.

  python scripts/contratos/generar_contrato.py --nit 900123456-7 --proveedor "Seguridad Industrial del Valle" \
      --firma 2026-11-20 --hasta 2027-11-19 --precio EPP-CASCO-001=17500 --precio EPP-GUANTE-002=20000 -o contrato.pdf
"""

import argparse
import zlib
from datetime import date, timedelta
from pathlib import Path

LINES_PER_PAGE = 46


def _escape(text: str) -> bytes:
    raw = text.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def contract_lines(nit: str, supplier: str, signed_on: str, valid_until: str | None, prices: list[tuple[str, str]],
                   title: str = "Contrato de suministro") -> list[str]:
    lines = [title.upper(), "", f"Proveedor: {supplier}", f"NIT: {nit}", f"Fecha de firma: {signed_on}"]
    if valid_until:
        lines.append(f"Vigente hasta: {valid_until}")
    lines += ["", "Precios pactados (COP por unidad, sin IVA):"]
    lines += [f"{sku}  {price}" for sku, price in prices]
    lines += ["", "Cláusulas:",
              "1. El proveedor mantiene los precios pactados durante la vigencia del contrato.",
              "2. Las entregas se coordinan con cada bodega y se registran como entradas de inventario.",
              "3. Cualquier cambio de precio requiere un nuevo contrato aprobado por el Líder."]
    return lines


def build_pdf(lines: list[str], compress: bool = True) -> bytes:
    pages = [lines[i:i + LINES_PER_PAGE] for i in range(0, max(len(lines), 1), LINES_PER_PAGE)]
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog_id = add(b"")
    pages_id = add(b"")
    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    page_ids = []
    for page_lines in pages:
        text = [b"BT", b"/F1 11 Tf", b"14 TL", b"56 790 Td"]
        for line in page_lines:
            text.append(b"(" + _escape(line) + b") Tj T*")
        text.append(b"ET")
        stream = b"\n".join(text)
        if compress:
            data = zlib.compress(stream)
            content_id = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream")
        else:
            content_id = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 %d 0 R >> >> "
                            b"/Contents %d 0 R >>" % (pages_id, font_id, content_id)))
    objects[catalog_id - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id
    kids = b" ".join(b"%d 0 R" % i for i in page_ids)
    objects[pages_id - 1] = b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % len(page_ids)

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, catalog_id, xref)
    return bytes(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--nit", default="900123456-7")
    parser.add_argument("--proveedor", default="Seguridad Industrial del Valle S.A.S.")
    parser.add_argument("--firma", default=date.today().isoformat())
    parser.add_argument("--hasta", default=(date.today() + timedelta(days=365)).isoformat())
    parser.add_argument("--precio", action="append", default=[], metavar="SKU=VALOR")
    parser.add_argument("--titulo", default="Contrato de suministro")
    parser.add_argument("-o", "--salida", default="contrato.pdf")
    args = parser.parse_args()
    prices = [tuple(p.split("=", 1)) for p in args.precio] or [("EPP-CASCO-001", "17.500,00"), ("EPP-GUANTE-002", "20.000,00")]
    pdf = build_pdf(contract_lines(args.nit, args.proveedor, args.firma, args.hasta, prices, args.titulo))
    Path(args.salida).write_bytes(pdf)
    print(f"{args.salida}: {len(pdf)} bytes")


if __name__ == "__main__":
    main()
