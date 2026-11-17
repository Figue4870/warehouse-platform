from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración de la API."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Fase 1: identidad de la bodega + PostgreSQL
    WAREHOUSE_ID: str = "WH-001"  # Identificador de esta bodega autónoma
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/warehouse_db"
    # True  -> la API crea las tablas al arrancar (útil corriendo sin Docker).
    # False -> las tablas las crea db/schema.sql (así está en Docker).
    AUTO_CREATE_TABLES: bool = True

    # Fase 2: caché con Redis (Cache-Aside)
    REDIS_URL: str = "redis://localhost:6379/0"
    CACHE_ENABLED: bool = True          # False = experimento "solo PostgreSQL"
    CACHE_TTL_SECONDS: int = 30         # Tiempo de vida de cada dato en caché
    REDIS_TIMEOUT_SECONDS: float = 0.5  # Si Redis no responde en este tiempo, se ignora
    REDIS_RETRY_AFTER_SECONDS: int = 5  # Tras un fallo, cuánto esperar antes de volver a intentar Redis


settings = Settings()
