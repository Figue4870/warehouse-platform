import socket

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5440/central_db"
    AUTO_CREATE_TABLES: bool = True               # en Docker = false (lo hace infrastructure/db/central_schema.sql)
    AMQP_URL: str = "amqp://platform:platform@localhost:5680/%2F"   # RabbitMQ CENTRAL
    RABBITMQ_MANAGEMENT_URL: str | None = None  # métricas de las colas; vacío = mismo host que AMQP_URL, puerto 15672
    INSTANCE_NAME: str = socket.gethostname()

    # Una bodega sin heartbeat hace más de esto se marca OFFLINE ("datos posiblemente desactualizados")
    ONLINE_AFTER_SECONDS: int = 45
    HEARTBEAT_POLL_SECONDS: int = 15              # cada cuánto la central le pregunta a cada bodega
    HEARTBEAT_TIMEOUT_SECONDS: float = 3.0        # espera máxima por la respuesta de una bodega
    # Clave del heartbeat de cada bodega: "WH-001=clave,WH-002=clave"
    WAREHOUSE_HEARTBEAT_SECRETS: str = ""
    # Cada cuánto el reconciliador revisa huecos de secuencia y bodegas nuevas
    RECONCILE_SECONDS: int = 15
    # Cuánto esperar antes de considerar que un hueco NO se va a llenar solo (y pedirlo por HTTP)
    GAP_GRACE_SECONDS: int = 20
    SYNC_HTTP_TIMEOUT_SECONDS: float = 5.0
    SYNC_PAGE_SIZE: int = 500
    LIVE_QUERY_TIMEOUT_SECONDS: float = 2.0       # consulta "en vivo" a las bodegas (alternativa A)
    # Opcional: "WH-001=http://10.0.1.10:8001,WH-002=..." (por defecto, http://lb_warehouse_<id>:80)
    WAREHOUSE_API_URLS: str = ""

    WORKER_MAX_RETRIES: int = 5
    WORKER_RETRY_DELAY_MS: int = 5000

    # Usuarios y roles. La central firma los tokens (Ed25519); las bodegas los verifican con la clave pública
    AUTH_ENABLED: bool = True
    AUTH_SIGNING_KEY: str = ""            # vacío = la central genera una y la guarda en su base de datos
    AUTH_TOKEN_TTL_MINUTES: int = 480     # una jornada: si la central cae, las sesiones abiertas siguen sirviendo
    SERVICE_TOKEN_TTL_MINUTES: int = 15   # token con el que la central llama a /sync y a las consultas en vivo
    IDENTITY_PROVIDER: str = "local"      # "local" (tabla users) o "ldap" (directorio tipo Active Directory)

    # Directorio LDAP. Con un Active Directory real: LDAP_USER_ATTRIBUTE=sAMAccountName, LDAP_USER_OBJECT_CLASS=user,
    # LDAP_GROUP_OBJECT_CLASS=group y LDAP_MANAGE_USERS=false (altas y contraseñas se hacen en el AD)
    LDAP_URL: str = "ldap://central_ldap:389"
    LDAP_BASE_DN: str = "dc=bodegas,dc=local"
    LDAP_BIND_DN: str = "cn=admin,dc=bodegas,dc=local"
    LDAP_BIND_PASSWORD: str = ""
    LDAP_USERS_OU: str = "ou=usuarios"
    LDAP_GROUPS_OU: str = "ou=grupos"
    LDAP_USER_ATTRIBUTE: str = "uid"
    LDAP_USER_OBJECT_CLASS: str = "inetOrgPerson"
    LDAP_GROUP_OBJECT_CLASS: str = "groupOfNames"
    LDAP_TIMEOUT_SECONDS: int = 5
    LDAP_MANAGE_USERS: bool = True
    # Grupo que da cada rol; {warehouse} se reemplaza por la bodega (WH-001)
    LDAP_GROUP_ADMIN_GLOBAL: str = "Administradores-Globales"
    LDAP_GROUP_LIDER: str = "Lideres"
    LDAP_GROUP_ADMIN_BODEGA: str = "Administradores-{warehouse}"
    LDAP_GROUP_REGISTRADOR: str = "Registradores-{warehouse}"
    # Si se define, los usuarios iniciales de la central (tabla users, created_by=seed) se crean en el directorio
    # con esta contraseña la primera vez que se usa. Vacío = no se crea ninguno.
    LDAP_SEED_PASSWORD: str = ""

    # Fase 6: Saga de transferencias
    SAGA_STEP_TIMEOUT_SECONDS: int = 60   # cuánto puede tardar una bodega en reservar / recibir
    SAGA_GRACE_SECONDS: int = 5           # margen extra antes de compensar (relojes, red)
    SAGA_TICK_SECONDS: float = 1.0        # cada cuánto el relay de comandos publica y revisa vencimientos
    SAGA_ALLOW_SIMULATION: bool = True    # permitir "simulate_failure" en POST /transfers (experimentos de la guía)

    # Reportes con Athena (exportación a S3)
    EXPORT_S3_BUCKET: str = ""            # vacío = se exporta a la carpeta EXPORT_DIR (desarrollo local)
    EXPORT_DIR: str = "exports"
    EXPORT_INTERVAL_SECONDS: int = 60
    EXPORT_BATCH_SIZE: int = 5000
    EXPORT_SNAPSHOT_SECONDS: int = 3600   # foto del inventario (tabla inventory de Athena)
    ATHENA_DATABASE: str = "warehouse_analytics"
    ATHENA_WORKGROUP: str = ""            # vacío = Athena no configurado (solo en AWS)
    ATHENA_MAX_ROWS: int = 1000
    AWS_DEFAULT_REGION: str = "us-east-1"

    # Batch nocturno (reporte diario de todas las bodegas)
    BATCH_HOUR: int = 23
    BATCH_MINUTE: int = 0
    BATCH_TIMEZONE: str = "America/Bogota"
    BATCH_POLL_SECONDS: int = 15
    BATCH_MAX_ATTEMPTS: int = 3

    # Lambda: S3 ObjectCreated -> validar, transformar, registrar y avisar a la central
    LAMBDA_FUNCTION_ARN: str = ""         # en AWS: la central conecta el bucket con esta función
    LAMBDA_SHARED_SECRET: str = ""        # firma HMAC de los avisos que la Lambda envía a la central
    CONTRACT_MAX_BYTES: int = 10 * 1024 * 1024

    LAMBDA_RESULT_WAIT_SECONDS: int = 60  # sin aviso de la Lambda en este tiempo, la central lee el resultado de S3

    # Sistemas externos (simulados en desarrollo)
    SUPPLIERS_API_URL: str = "http://external_mocks:8000/proveedores"
    SERVIENTREGA_API_URL: str = "http://external_mocks:8000/servientrega"
    SERVIENTREGA_WEBHOOK_SECRET: str = ""
    EXTERNAL_TIMEOUT_SECONDS: float = 2.0
    CENTRAL_CALLBACK_URL: str = "http://central_api:8000"   # dirección con la que los sistemas externos avisan
    SHIPPING_ENABLED: bool = False        # la Saga contrata el transporte con Servientrega entre la reserva y la recepción
    SHIPPING_POLL_SECONDS: int = 10
    SHIPPING_MAX_ATTEMPTS: int = 5

    # Fase 12: extensión IoT (consultas y alertas de sensores)
    IOT_API_URL: str = "http://iot_api:8000"
    IOT_SHARED_SECRET: str = ""


settings = Settings()


def configured_api_urls() -> dict[str, str]:
    urls = {}
    for item in filter(None, (part.strip() for part in settings.WAREHOUSE_API_URLS.split(","))):
        warehouse_id, url = item.split("=", 1)
        urls[warehouse_id.strip()] = url.strip().rstrip("/")
    return urls


def default_api_url(warehouse_id: str) -> str:
    """Balanceador de la bodega en la red de Docker."""
    return f"http://lb_warehouse_{warehouse_id}:80"


def heartbeat_secrets() -> dict[str, str]:
    """Clave del heartbeat por bodega, leída de WAREHOUSE_HEARTBEAT_SECRETS."""
    from platform_common.secure_heartbeat import parse_secrets

    return parse_secrets(settings.WAREHOUSE_HEARTBEAT_SECRETS)


def warehouse_registry() -> dict[str, dict]:
    """Bodegas que la central consulta: URL de su API y clave del heartbeat (None si no la tiene)."""
    urls, keys = configured_api_urls(), heartbeat_secrets()
    return {wh: {"api_url": urls.get(wh) or default_api_url(wh), "secret": keys.get(wh)}
            for wh in sorted(set(urls) | set(keys))}
