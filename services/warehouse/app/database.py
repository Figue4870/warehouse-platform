import threading

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

# pool_pre_ping: antes de usar una conexión del pool verifica que siga viva.
# pool_timeout corto: con el pool agotado es mejor responder 503 rápido que acumular peticiones.
_pool = {} if settings.DATABASE_URL.startswith("sqlite") else {
    "pool_size": settings.DB_POOL_SIZE, "max_overflow": settings.DB_MAX_OVERFLOW, "pool_timeout": settings.DB_POOL_TIMEOUT_SECONDS}
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, **_pool)
# expire_on_commit=False: después del commit no se vuelve a consultar la base para leer lo que ya se tiene
SessionLocal = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=engine)
Base = declarative_base()


async def get_db():
    """Dependencia de FastAPI: una sesión de base de datos por request.

    Es async para que el cierre (devolver la conexión al pool) corra en el event loop. Si fuera síncrona, el
    cierre esperaría un hilo libre del threadpool; bajo carga alta todos los hilos esperan conexiones que solo
    ese cierre libera y la API se bloquea (hallazgo de la Fase 11).
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class SqlQueryCounter:
    """Cuenta cuántas sentencias SQL ejecuta este proceso de la API."""

    def __init__(self) -> None:
        self._value = 0
        self._lock = threading.Lock()

    def increment(self) -> None:
        with self._lock:
            self._value += 1

    def reset(self) -> None:
        with self._lock:
            self._value = 0

    @property
    def value(self) -> int:
        return self._value


sql_query_counter = SqlQueryCounter()


@event.listens_for(Engine, "before_cursor_execute")
def _count_sql_queries(conn, cursor, statement, parameters, context, executemany):
    sql_query_counter.increment()
