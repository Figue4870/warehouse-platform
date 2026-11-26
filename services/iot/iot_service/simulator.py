"""Simulador de sensores (Fase 12): N sensores repartidos en varias bodegas publicando por MQTT (QoS 1).

  python -m iot_service.simulator --broker localhost --port 1883 --warehouses WH-001,WH-002,WH-003,WH-004,WH-005 \
      --sensors-per-warehouse 20 --interval 2 --duration 180 --duplicates 0.02 --reorder 0.02 \
      --network-drop 40:20 --anomalies --report simulacion.json

Fallas que puede inyectar:
  --duplicates   fracción de lecturas que el sensor reenvía (como un reintento sin PUBACK)
  --reorder      fracción de lecturas que salen después de la siguiente (fuera de orden)
  --network-drop INICIO:DURACIÓN  segundos sin red: cada sensor guarda en su memoria y envía todo al volver
  --anomalies    temperatura, humedad, energía, puerta y movimiento fuera de lo normal en algunos sensores
"""

import argparse
import json
import random
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timezone

from iot_service.mqtt import MqttClient, MqttError
from iot_service.readings import MQTT_TOPIC, UNITS
from iot_service.rules import after_hours, limits

MIX = (("temperature", 6), ("humidity", 4), ("motion", 4), ("door", 3), ("energy", 3))
PREFIX = {"temperature": "TEMP", "humidity": "HUM", "motion": "MOV", "door": "DOOR", "energy": "ENER"}


class Sensor:
    def __init__(self, warehouse_id: str, sensor_type: str, number: int, anomalous: bool):
        self.warehouse_id, self.sensor_type, self.anomalous = warehouse_id, sensor_type, anomalous
        self.sensor_id = f"{PREFIX[sensor_type]}-{warehouse_id[-3:]}-{number:03d}"
        self.sequence = 0
        self.door_open = False

    def value(self, elapsed: float) -> float:
        anomaly = self.anomalous and elapsed > 20
        if self.sensor_type == "temperature":
            return round(random.gauss(34 if anomaly else 21, 0.8), 2)
        if self.sensor_type == "humidity":
            return round(random.gauss(82 if anomaly else 55, 2), 1)
        if self.sensor_type == "energy":
            return round(random.gauss(19 if anomaly else 6, 0.7), 2)
        if self.sensor_type == "door":
            if anomaly:
                return 1.0  # se quedó abierta
            self.door_open = random.random() < (0.7 if self.door_open else 0.1)
            return float(self.door_open)
        if anomaly:
            return float(random.random() < 0.5)  # alguien se mueve en la bodega (alerta solo fuera del horario)
        lim = limits()
        return 0.0 if after_hours(datetime.now(timezone.utc), lim["night_from"], lim["night_to"]) else float(random.random() < 0.15)

    def reading(self, elapsed: float) -> dict:
        self.sequence += 1
        return {"event_id": f"EVT-{uuid.uuid4().hex[:16]}", "sequence_number": self.sequence,
                "warehouse_id": self.warehouse_id, "sensor_id": self.sensor_id, "sensor_type": self.sensor_type,
                "value": self.value(elapsed), "unit": UNITS[self.sensor_type],
                "timestamp": datetime.now(timezone.utc).isoformat()}


def run_warehouse(warehouse_id: str, sensors: list[Sensor], args, start: float, stats: Counter, lock: threading.Lock):
    client = MqttClient(args.broker, args.port, f"sim-{warehouse_id}-{uuid.uuid4().hex[:6]}", args.username, args.password)
    local = Counter()
    pending: list[dict] = []          # lo que los sensores guardan mientras no hay red
    held: dict[str, dict] = {}        # lectura retenida para salir fuera de orden
    drop_from, drop_seconds = args.network_drop
    connected = False
    while (elapsed := time.monotonic() - start) < args.duration:
        tick = time.monotonic()
        offline = drop_seconds and drop_from <= elapsed < drop_from + drop_seconds
        batch = []
        for sensor in sensors:
            reading = sensor.reading(elapsed)
            if sensor.sensor_id in held:
                batch.append(reading)
                batch.append(held.pop(sensor.sensor_id))
                local["reordered"] += 1
            elif random.random() < args.reorder:
                held[sensor.sensor_id] = reading
            else:
                batch.append(reading)
            local["unique"] += 1
        if offline:
            if connected:
                client.close()
                connected = False
            pending.extend(batch)
            local["buffered_on_sensor"] += len(batch)
        else:
            outgoing = pending + batch
            pending = []
            try:
                if not connected:
                    client.connect()
                    connected = True
                for reading in outgoing:
                    body = json.dumps(reading).encode()
                    topic = MQTT_TOPIC.format(**reading)
                    client.publish(topic, body)
                    local["published"] += 1
                    if random.random() < args.duplicates:
                        client.publish(topic, body)
                        local["duplicates_sent"] += 1
            except (MqttError, OSError):
                connected = False
                local["publish_errors"] += 1
                pending = outgoing  # se reintenta en la próxima vuelta
        time.sleep(max(0.0, args.interval - (time.monotonic() - tick)))
    for reading in list(held.values()) + pending:
        try:
            if not connected:
                client.connect()
                connected = True
            client.publish(MQTT_TOPIC.format(**reading), json.dumps(reading).encode())
            local["published"] += 1
        except (MqttError, OSError):
            local["lost_on_sensor"] += 1
    client.close()
    with lock:
        stats.update(local)
        stats[f"unique:{warehouse_id}"] += local["unique"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--broker", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username", default="iot")
    parser.add_argument("--password", default="iot")
    parser.add_argument("--warehouses", default="WH-001,WH-002,WH-003,WH-004,WH-005")
    parser.add_argument("--sensors-per-warehouse", type=int, default=20)
    parser.add_argument("--interval", type=float, default=2.0, help="Segundos entre lecturas de cada sensor")
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--duplicates", type=float, default=0.0)
    parser.add_argument("--reorder", type=float, default=0.0)
    parser.add_argument("--network-drop", default="0:0", help="INICIO:DURACIÓN en segundos")
    parser.add_argument("--anomalies", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    args.network_drop = tuple(float(x) for x in args.network_drop.split(":"))

    warehouses = [w.strip() for w in args.warehouses.split(",") if w.strip()]
    stats, lock, threads = Counter(), threading.Lock(), []
    started_at = datetime.now(timezone.utc)
    start = time.monotonic()
    for index, warehouse_id in enumerate(warehouses):
        sensors, number = [], 1
        for sensor_type, count in MIX:
            for _ in range(max(1, round(count * args.sensors_per_warehouse / 20))):
                # Con --anomalies, en la primera bodega fallan el primer sensor de temperatura, humedad, movimiento, puerta y energía
                sensors.append(Sensor(warehouse_id, sensor_type, number, args.anomalies and index == 0 and number in (1, 7, 11, 15, 18)))
                number += 1
        thread = threading.Thread(target=run_warehouse, args=(warehouse_id, sensors, args, start, stats, lock))
        threads.append(thread)
        thread.start()
    for thread in threads:
        thread.join()
    report = {"started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
              "warehouses": warehouses, "sensors": args.sensors_per_warehouse * len(warehouses), "interval_s": args.interval,
              "duration_s": args.duration, "network_drop": args.network_drop, **dict(stats)}
    print(json.dumps(report, indent=2))
    if args.report:
        with open(args.report, "w", encoding="utf-8") as out:
            json.dump(report, out, indent=2)


if __name__ == "__main__":
    main()
