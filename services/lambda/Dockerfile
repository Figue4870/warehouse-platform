# Emulación local de la Lambda (en AWS el mismo código corre como función, sin este contenedor)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY storage_processor ./storage_processor
COPY local_runner.py .

CMD ["python", "local_runner.py"]
