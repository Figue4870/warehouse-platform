"""Lectura de PDF sin dependencias: número de páginas y texto de los operadores Tj/TJ.

Soporta los filtros FlateDecode, ASCII85Decode y ASCIIHexDecode, objetos comprimidos (ObjStm) y fuentes con
tabla ToUnicode, que es como exportan Word, LibreOffice y ReportLab. Un PDF escaneado (imagen) no trae texto: en
ese caso vale la metadata del formulario.
"""

import base64
import binascii
import re
import zlib

OBJECT = re.compile(rb"(\d+)\s+(\d+)\s+obj\b(.*?)\bendobj", re.S)
STREAM = re.compile(rb"^(.*?)\bstream\r?\n(.*?)\r?\n?endstream", re.S)
PAGE = re.compile(rb"/Type\s*/Page(?![a-zA-Z])")
FONT_ENTRY = re.compile(rb"/([A-Za-z0-9_.+-]+)\s+(\d+)\s+0\s+R")
ESCAPES = {ord("n"): b"\n", ord("r"): b"\r", ord("t"): b"\t", ord("b"): b"\b", ord("f"): b"\f",
           ord("("): b"(", ord(")"): b")", ord("\\"): b"\\"}
NUMBER = re.compile(rb"[+-]?(?:\d+\.?\d*|\.\d+)")
LINE_BREAK_OPERATORS = {b"Td", b"TD", b"T*", b"ET", b"'", b'"'}


def is_pdf(data: bytes) -> bool:
    return data.startswith(b"%PDF-")


def is_complete(data: bytes) -> bool:
    return b"%%EOF" in data[-2048:]


# objetos y flujos
def _decode(header: bytes, raw: bytes) -> bytes | None:
    filters = re.findall(rb"/(FlateDecode|ASCII85Decode|ASCIIHexDecode|[A-Za-z0-9]+Decode)", header.split(b"/DecodeParms")[0])
    for name in filters:
        try:
            if name == b"FlateDecode":
                raw = zlib.decompressobj().decompress(raw)
            elif name == b"ASCII85Decode":
                raw = base64.a85decode(re.sub(rb"\s", b"", raw).removeprefix(b"<~").split(b"~>")[0])
            elif name == b"ASCIIHexDecode":
                hexdata = re.sub(rb"[^0-9A-Fa-f]", b"", raw.split(b">")[0])
                raw = binascii.unhexlify(hexdata + (b"0" if len(hexdata) % 2 else b""))
            else:
                return None  # imágenes (DCT, JBIG2...): no llevan texto
        except (zlib.error, ValueError, binascii.Error):
            return None
    return raw


def read_objects(data: bytes) -> dict[int, tuple[bytes, bytes | None]]:
    """{número: (diccionario, flujo decodificado o None)}, incluidos los que vienen dentro de ObjStm."""
    objects = {}
    for match in OBJECT.finditer(data):
        body = match.group(3)
        stream = STREAM.match(body)
        if stream:
            objects[int(match.group(1))] = (stream.group(1), _decode(stream.group(1), stream.group(2)))
        else:
            objects[int(match.group(1))] = (body, None)
    for header, content in list(objects.values()):
        if content is None or b"/ObjStm" not in header:
            continue
        count = re.search(rb"/N\s+(\d+)", header)
        first = re.search(rb"/First\s+(\d+)", header)
        if not count or not first:
            continue
        numbers = list(map(int, content[:int(first.group(1))].split()))
        pairs = list(zip(numbers[0::2], numbers[1::2]))[:int(count.group(1))]
        for index, (number, offset) in enumerate(pairs):
            start = int(first.group(1)) + offset
            end = int(first.group(1)) + pairs[index + 1][1] if index + 1 < len(pairs) else len(content)
            objects.setdefault(number, (content[start:end], None))
    return objects


def page_count(data: bytes) -> int:
    return sum(1 for header, _ in read_objects(data).values() if PAGE.search(header)) or len(PAGE.findall(data))


# tablas ToUnicode
def _utf16(hexstr: bytes) -> str:
    raw = binascii.unhexlify(hexstr)
    return raw.decode("utf-16-be", errors="replace") if len(raw) % 2 == 0 else raw.decode("latin-1")


def parse_cmap(cmap: bytes) -> tuple[dict[int, str], int]:
    mapping, width = {}, 1
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", cmap, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            mapping[int(src, 16)] = _utf16(dst)
            width = max(width, len(src) // 2)
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", cmap, re.S):
        for start, end, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]+>|\[[^\]]*\])", block):
            width = max(width, len(start) // 2)
            low, high = int(start, 16), int(end, 16)
            if dst.startswith(b"["):
                for offset, item in enumerate(re.findall(rb"<([0-9A-Fa-f]+)>", dst)):
                    mapping[low + offset] = _utf16(item)
            else:
                base = binascii.unhexlify(dst[1:-1])
                for offset in range(min(high - low + 1, 65536)):
                    value = int.from_bytes(base, "big") + offset
                    mapping[low + offset] = value.to_bytes(len(base), "big").decode("utf-16-be", errors="replace")
    return mapping, width


def font_maps(objects: dict) -> dict[bytes, tuple[dict[int, str], int]]:
    """Nombre del recurso de fuente (/F1, /TT0...) -> su tabla ToUnicode."""
    cmaps = {}
    for number, (header, _) in objects.items():
        ref = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", header)
        if ref and int(ref.group(1)) in objects and objects[int(ref.group(1))][1]:
            cmaps[number] = parse_cmap(objects[int(ref.group(1))][1])
    names = {}
    for header, _ in objects.values():
        for font_dict in re.findall(rb"/Font\s*<<(.*?)>>", header, re.S):
            for name, number in FONT_ENTRY.findall(font_dict):
                if int(number) in cmaps:
                    names.setdefault(name, cmaps[int(number)])
        indirect = re.search(rb"/Font\s+(\d+)\s+0\s+R", header)
        if indirect and int(indirect.group(1)) in objects:
            for name, number in FONT_ENTRY.findall(objects[int(indirect.group(1))][0]):
                if int(number) in cmaps:
                    names.setdefault(name, cmaps[int(number)])
    return names


# operadores de texto
def _literal(stream: bytes, start: int) -> tuple[bytes, int]:
    out, depth, i = bytearray(), 1, start + 1
    while i < len(stream):
        ch = stream[i]
        if ch == 0x5C:
            nxt = stream[i + 1] if i + 1 < len(stream) else None
            if nxt in ESCAPES:
                out += ESCAPES[nxt]
                i += 2
                continue
            octal = re.match(rb"[0-7]{1,3}", stream[i + 1:i + 4])
            if octal:
                out.append(int(octal.group(0), 8) & 0xFF)
                i += 1 + len(octal.group(0))
                continue
            i += 2 if nxt in (0x0A, 0x0D) else 1
            continue
        if ch == 0x28:
            depth += 1
        elif ch == 0x29:
            depth -= 1
            if depth == 0:
                return bytes(out), i + 1
        out.append(ch)
        i += 1
    return bytes(out), i


def _show(raw: bytes, font) -> str:
    if font is None:
        return raw.decode("cp1252", errors="replace")
    mapping, width = font
    codes = (int.from_bytes(raw[i:i + width], "big") for i in range(0, len(raw) - width + 1, width))
    return "".join(mapping.get(code, "") for code in codes)


def stream_text(stream: bytes, fonts: dict) -> str:
    lines, current, pending, operands = [], [], [], []
    font, i, in_array = None, 0, False
    while i < len(stream):
        ch = stream[i]
        if ch == 0x28:  # (cadena literal)
            value, i = _literal(stream, i)
            pending.append(_show(value, font))
        elif ch == 0x3C and stream[i + 1:i + 2] != b"<":  # <cadena hex>
            end = stream.find(b">", i)
            hexdata = re.sub(rb"\s", b"", stream[i + 1:end])
            try:
                pending.append(_show(binascii.unhexlify(hexdata + (b"0" if len(hexdata) % 2 else b"")), font))
            except binascii.Error:
                pass
            i = end + 1
        elif ch == 0x2F:  # /Nombre
            match = re.compile(rb"/([^\s()<>\[\]{}/%]*)").match(stream, i)
            operands.append(match.group(1))
            i = match.end()
        elif chr(ch) in "0123456789.-+":
            match = NUMBER.match(stream, i)
            i = match.end() if match and match.end() > i else i + 1
            if in_array and match:
                try:
                    if float(match.group(0)) < -180:  # desplazamiento grande dentro de TJ = espacio entre palabras
                        pending.append(" ")
                except ValueError:
                    pass
        elif ch in b"[]<>{}" or chr(ch).isspace():
            in_array = True if ch == 0x5B else False if ch == 0x5D else in_array
            i += 1
        elif ch == 0x25:  # comentario
            end = stream.find(b"\n", i)
            i = len(stream) if end < 0 else end + 1
        else:
            match = re.compile(rb"[A-Za-z*'\"]+").match(stream, i)
            op = match.group(0) if match else b""
            i = match.end() if match else i + 1
            if op == b"Tf" and operands:
                font = fonts.get(operands[-1])
            elif op in (b"Tj", b"TJ"):
                current.extend(pending)
            if op in (b"'", b'"'):
                lines.append("".join(current))
                current = list(pending)
            elif op in LINE_BREAK_OPERATORS and current:
                lines.append("".join(current))
                current = []
            pending, operands = [], []
    if current:
        lines.append("".join(current))
    return "\n".join(line.strip() for line in lines if line.strip())


def extract_text(data: bytes) -> str:
    objects = read_objects(data)
    fonts = font_maps(objects)
    contents = []
    for header, stream in objects.values():
        if stream is None or b"/ObjStm" in header or b"/XRef" in header or b"/Subtype" in header or b"begincmap" in stream:
            continue
        if b"BT" in stream and (b"Tj" in stream or b"TJ" in stream):
            contents.append(stream_text(stream, fonts))
    return "\n".join(filter(None, contents))
