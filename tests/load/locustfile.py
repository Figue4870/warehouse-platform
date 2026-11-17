"""Alternativa con Locust (interfaz web con gráficas en tiempo real)."""

import os

from locust import HttpUser, between, task

PRODUCT_ID = int(os.getenv("PRODUCT_ID", "1"))


class InventoryReader(HttpUser):
    wait_time = between(0.01, 0.1)

    @task(10)
    def read_inventory(self):
        # 10 lecturas por cada escritura: el escenario "alta lectura" del README (WH-002)
        self.client.get(f"/inventory/{PRODUCT_ID}", name="GET /inventory/{id}")

    @task(1)
    def receive_one(self):
        self.client.post(
            "/inventory/movements",
            json={"product_id": PRODUCT_ID, "type": "RECEIPT", "quantity": 1, "reference": "locust"},
            name="POST /inventory/movements",
        )
