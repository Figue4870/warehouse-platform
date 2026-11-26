# Fase 12: extensión IoT (gateway de borde, ingesta, workers y simulador). Contexto de build: services/
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/common
WORKDIR /app
COPY iot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common/platform_common /opt/common/platform_common
COPY iot/iot_service ./iot_service

EXPOSE 8000
# Cada contenedor elige su papel: api (ingesta y consultas), gateway, worker <storage|alerts|analytics>, simulator
CMD ["uvicorn", "iot_service.api:app", "--host", "0.0.0.0", "--port", "8000"]
