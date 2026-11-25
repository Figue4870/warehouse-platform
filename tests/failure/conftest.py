"""Pruebas de fallos (Fase 10): apagan y prenden componentes reales. Solo corren con FAILURE_TESTS=1.

CHAOS_MODE=docker (por defecto) usa `docker stop/start` con los nombres de contenedor de los compose.
CHAOS_MODE=command ejecuta CHAOS_COMMAND con {action} y {component} (para otros entornos de prueba).
"""

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

from it_helpers import ADMIN_GLOBAL, ADMIN_WH1, API_URL, CENTRAL_URL, client, local_product, reachable, universal_sku  # noqa: E402

WH = os.getenv("CHAOS_WAREHOUSE", "WH-001")
CONTAINERS = {
    "redis": [f"redis_warehouse_{WH}"],
    "rabbitmq": [f"rabbitmq_warehouse_{WH}"],
    "notification_worker": [f"notification_worker_{WH}"],
    "central": ["central_api", "central_projector", "central_reconciler", "central_heartbeat"],
}


class Chaos:
    def __init__(self):
        self.mode = os.getenv("CHAOS_MODE", "docker")
        self.stopped = []

    def _run(self, action, component):
        if self.mode == "command":
            command = os.environ["CHAOS_COMMAND"].format(action=action, component=component)
            subprocess.run(shlex.split(command), check=True, timeout=120)
        else:
            subprocess.run(["docker", action, *CONTAINERS[component]], check=True, timeout=120, capture_output=True)

    def stop(self, component):
        self._run("stop", component)
        self.stopped.append(component)

    def start(self, component):
        self._run("start", component)
        if component in self.stopped:
            self.stopped.remove(component)

    def restore(self):
        for component in list(self.stopped):
            self.start(component)


def pytest_collection_modifyitems(config, items):
    if os.getenv("FAILURE_TESTS") != "1":
        skip = pytest.mark.skip(reason="Apagan componentes reales: activar con FAILURE_TESTS=1")
        for item in items:
            if "failure" in str(item.fspath):
                item.add_marker(skip)


@pytest.fixture
def chaos():
    controller = Chaos()
    yield controller
    controller.restore()


@pytest.fixture(scope="session")
def central():
    if not reachable(CENTRAL_URL):
        pytest.skip(f"La central no responde en {CENTRAL_URL}")
    with client(CENTRAL_URL, ADMIN_GLOBAL) as c:
        yield c


@pytest.fixture(scope="session")
def api(central):
    if not reachable(API_URL):
        pytest.skip(f"La API no responde en {API_URL}")
    with client(API_URL, ADMIN_WH1) as c:
        yield c


@pytest.fixture
def product(api, central):
    return local_product(api, universal_sku(central, "FT"), initial_stock=20)
