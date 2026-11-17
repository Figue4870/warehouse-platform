FROM python:3.11-slim

# Logs inmediatos (sin buffer) y sin archivos .pyc dentro del contenedor
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Primero solo requirements: si no cambian, Docker reutiliza esta capa (build más rápido)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./app ./app
COPY main.py main.py

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
