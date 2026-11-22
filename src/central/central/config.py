import socket

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5440/central_db"
    AUTO_CREATE_TABLES: bool = True               # en Docker = false (lo hace db/central_schema.sql)
    AMQP_URL: str = "amqp://platform:platform@localhost:5680/%2F"   # RabbitMQ CENTRAL
    INSTANCE_NAME: str = socket.gethostname()

    # Una bodega sin heartbeat hace más de esto se marca OFFLINE ("datos posiblemente desactualizados")
    ONLINE_AFTER_SECONDS: int = 45
    # Cada cuánto el reconciliador revisa huecos de secuencia y bodegas nuevas
    RECONCILE_SECONDS: int = 15
    # Cuánto esperar antes de considerar que un hueco NO se va a llenar solo (y pedirlo por HTTP)
    GAP_GRACE_SECONDS: int = 20
    SYNC_HTTP_TIMEOUT_SECONDS: float = 5.0
    SYNC_PAGE_SIZE: int = 500
    LIVE_QUERY_TIMEOUT_SECONDS: float = 2.0       # consulta "en vivo" a las bodegas (alternativa A)
    # Opcional: "WH-001=http://10.0.1.10:8001,WH-002=..." (si no, se aprenden de los heartbeats)
    WAREHOUSE_API_URLS: str = ""

    SAGA_ALLOW_SIMULATION: bool = False
    EXPORT_DIRECTORY: str = "/app/exports"
    EXPORT_S3_BUCKET: str = ""
    ATHENA_DATABASE: str = ""
    ATHENA_WORKGROUP: str = ""

    WORKER_MAX_RETRIES: int = 5
    WORKER_RETRY_DELAY_MS: int = 5000


settings = Settings()


def configured_api_urls() -> dict[str, str]:
    urls = {}
    for item in filter(None, (part.strip() for part in settings.WAREHOUSE_API_URLS.split(","))):
        warehouse_id, url = item.split("=", 1)
        urls[warehouse_id.strip()] = url.strip().rstrip("/")
    return urls
