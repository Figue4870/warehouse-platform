"""Agrega una bodega nueva copiando la configuración de la última y moviendo sus puertos."""

import re
import secrets
import sys
from pathlib import Path

PORT_KEYS = ["DB_PORT", "BE_PORT", "FE_PORT", "REDIS_PORT", "RABBIT_PORT", "RABBIT_MGMT_PORT"]
COMPONENTS = Path(__file__).resolve().parent.parent / "docker" / "components"


class WarehouseError(ValueError):
    pass


def used_ports(components: Path) -> set[int]:
    """Puertos que ya usa cualquier componente (bodegas y lo que se agregue después) en este PC."""
    ports = set()
    for env in components.glob("*/.env"):
        ports |= {int(port) for port in re.findall(r"^\w+_PORT=(\d+)$", env.read_text(encoding="utf-8"), re.M)}
    return ports


def create_warehouse(wh_id: str, components: Path = COMPONENTS) -> Path:
    """Crea components/wh_NNN/.env a partir de la bodega con número más alto. Devuelve la ruta del .env."""
    if not re.fullmatch(r"WH-\d{3}", wh_id):
        raise WarehouseError("El código debe ser WH-NNN, por ejemplo WH-004")
    target = components / f"wh_{wh_id[3:]}"
    if target.exists():
        raise WarehouseError(f"Ya existe {target}")
    existing = sorted(p for p in components.iterdir() if p.is_dir() and re.fullmatch(r"wh_\d{3}", p.name))
    if not existing:
        raise WarehouseError(f"No hay ninguna bodega en {components} para copiar")
    template = existing[-1]
    text = (template / ".env").read_text(encoding="utf-8")
    old_id = re.search(r"^WH_ID=(.+)$", text, re.M).group(1).strip()
    step = int(wh_id[3:]) - int(old_id[3:])
    text = text.replace(old_id, wh_id)
    used = used_ports(components)
    for key in PORT_KEYS:
        match = re.search(rf"^{key}=(\d+)$", text, re.M)
        if match is None:
            continue
        port = int(match.group(1)) + step
        # Con muchas bodegas los rangos se cruzan.
        # si el puerto ya está ocupado se toma el siguiente libre.
        while port in used:
            port += 1
        used.add(port)
        text = re.sub(rf"^{key}=\d+$", f"{key}={port}", text, flags=re.M)
    # Cada bodega tiene su propia clave del heartbeat (no se copia la de la bodega anterior)
    secret = f"dev-{wh_id.lower().replace('-', '')}-{secrets.token_urlsafe(18)}"
    if re.search(r"^HEARTBEAT_SECRET=.*$", text, re.M):
        text = re.sub(r"^HEARTBEAT_SECRET=.*$", f"HEARTBEAT_SECRET={secret}", text, flags=re.M)
    else:
        text = text.rstrip("\n") + f"\nHEARTBEAT_SECRET={secret}\n"
    target.mkdir()
    env_file = target / ".env"
    env_file.write_text(text, encoding="utf-8")
    register_in_central(components, wh_id, secret)
    return env_file


def register_in_central(components: Path, wh_id: str, secret: str) -> bool:
    """Agrega (o reemplaza) la clave de la bodega en WAREHOUSE_HEARTBEAT_SECRETS de la central."""
    central_env = components / "central" / ".env"
    if not central_env.exists():
        return False
    text = central_env.read_text(encoding="utf-8")
    match = re.search(r"^WAREHOUSE_HEARTBEAT_SECRETS=(.*)$", text, re.M)
    if match is None:
        text = text.rstrip("\n") + f"\nWAREHOUSE_HEARTBEAT_SECRETS={wh_id}={secret}\n"
    else:
        entries = [e for e in match.group(1).split(",") if e.strip() and not e.strip().startswith(f"{wh_id}=")]
        line = "WAREHOUSE_HEARTBEAT_SECRETS=" + ",".join(entries + [f"{wh_id}={secret}"])
        text = text[:match.start()] + line + text[match.end():]
    central_env.write_text(text, encoding="utf-8")
    return True


def main():
    if len(sys.argv) != 2:
        sys.exit("Uso: python scripts/new_warehouse.py WH-004")
    try:
        env_file = create_warehouse(sys.argv[1])
    except WarehouseError as error:
        sys.exit(str(error))
    text = env_file.read_text(encoding="utf-8")
    print(f"Creada {env_file}:")
    print("\n".join(line for line in text.splitlines() if line.split("=")[0] in PORT_KEYS + ["WH_ID"]))
    print("Clave del heartbeat generada y agregada al registro de la central (docker/components/central/.env).")
    print("Vuelve a levantar la central para que lea la clave: .\\manage.ps1 start central")


if __name__ == "__main__":
    main()
