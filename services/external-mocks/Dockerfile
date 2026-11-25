# Sistemas externos simulados (Proveedores y Servientrega) para desarrollo y pruebas de fallos
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mocks.py .

EXPOSE 8000
CMD ["uvicorn", "mocks:app", "--host", "0.0.0.0", "--port", "8000"]
