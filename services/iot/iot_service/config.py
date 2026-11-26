from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    INSTANCE_NAME: str = "iot"
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5450/iot_db"
    AUTO_CREATE_TABLES: bool = False                  # en Docker las tablas las crea infrastructure/db/iot_schema.sql
    AMQP_URL: str = "amqp://iot:iot@localhost:5690/%2F"
    RABBITMQ_MANAGEMENT_URL: str | None = None

    # Gateway de borde
    GATEWAY_WAREHOUSES: str = ""                      # vacío = todas las bodegas que publiquen en el broker
    GATEWAY_BUFFER_PATH: str = "/data/gateway-buffer.sqlite3"
    GATEWAY_STATS_PORT: int = 8001
    GATEWAY_BATCH_SIZE: int = 200
    GATEWAY_FLUSH_SECONDS: float = 0.5
    GATEWAY_DEDUPE_WINDOW: int = 512                  # últimas secuencias recordadas por sensor
    INGESTION_URL: str = "http://iot_api:8000"
    GATEWAY_KEY: str = ""                             # si se define, la ingesta solo acepta lotes con esta clave
    INGESTION_TIMEOUT_SECONDS: float = 3.0

    # Workers
    WORKER_MAX_RETRIES: int = 5
    WORKER_RETRY_DELAY_MS: int = 3000

    # Alertas: aviso firmado a la central (notificación al administrador de la bodega)
    CENTRAL_API_URL: str = "http://central_api:8000"
    IOT_SHARED_SECRET: str = ""


settings = Settings()
