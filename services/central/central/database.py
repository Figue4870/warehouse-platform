from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from central.config import settings

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


async def get_db():
    """Async: el cierre devuelve la conexión al pool sin esperar un hilo libre del threadpool (Fase 11)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
