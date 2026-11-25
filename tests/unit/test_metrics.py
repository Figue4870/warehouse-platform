"""Fase 11: métricas en formato Prometheus y su resumen (RPS, P50/P95/P99, errores)."""

import re

from platform_common.metrics import Histogram, Registry


def test_cuantiles_del_histograma():
    histogram = Histogram("x", "x", buckets=(0.01, 0.1, 1.0))
    for _ in range(90):
        histogram.observe(0.005)
    for _ in range(10):
        histogram.observe(0.5)
    assert histogram.quantile(0.5) <= 0.01
    assert 0.1 < histogram.quantile(0.99) <= 1.0
    assert Histogram("y", "y").quantile(0.5) is None


def test_formato_de_texto_de_prometheus():
    registry = Registry("prueba")
    registry.observe_request("GET", "/inventory/{product_id}", 200, 0.02)
    registry.observe_request("GET", "/inventory/{product_id}", 503, 0.4)
    text = registry.render()
    assert 'http_requests_total{method="GET",route="/inventory/{product_id}",status="200"} 1' in text
    assert re.search(r'http_request_duration_seconds_bucket\{le="\+Inf",method="GET",route="/inventory/\{product_id\}"\} 2', text)
    summary = registry.summary()
    assert summary["requests"] == 2 and summary["error_rate"] == 0.5


def test_un_colector_caido_no_tumba_las_metricas():
    registry = Registry("prueba")

    def broken():
        raise ConnectionError("RabbitMQ no responde")

    registry.add_collector(broken)
    assert 'collector_errors_total{collector="broken",error="ConnectionError"} 1' in registry.render()


def served(text: str) -> float:
    match = re.search(r'http_requests_total\{method="GET",route="/inventory/\{product_id\}",status="200"\} (\S+)', text)
    return float(match.group(1)) if match else 0.0


def test_la_api_de_bodega_publica_metricas_por_ruta(client, create_product):
    product = create_product()
    before = served(client.get("/metrics/prometheus").text)
    for _ in range(3):
        client.get(f"/inventory/{product['id']}")
    text = client.get("/metrics/prometheus").text
    assert served(text) - before == 3                                   # una serie por ruta, no por id
    assert f"/inventory/{product['id']}" not in text
    assert "http_request_duration_seconds_bucket" in text
    summary = client.get("/metrics").json()
    assert summary["http"]["latency_ms"]["p95"] is not None and summary["cache"]["hits"] >= 2
