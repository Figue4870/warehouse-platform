# Contexto de build: la carpeta services/ (para copiar la librería compartida services/common)
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/common

WORKDIR /app

COPY central/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/platform_common /opt/common/platform_common
COPY central/central ./central
COPY web-ui ./central/static/ui

EXPOSE 8000

# La API central. Los workers usan la misma imagen:
#   python -m central.workers.projector | central.workers.reconciler
CMD ["uvicorn", "central.api:app", "--host", "0.0.0.0", "--port", "8000"]
