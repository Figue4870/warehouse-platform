"""Importa todos los modelos de SQLAlchemy."""

from app.events import models as events_models  # noqa: F401
from app.modules.alerts import models as alerts_models  # noqa: F401
from app.modules.inventory import models as inventory_models  # noqa: F401
from app.modules.products import models as products_models  # noqa: F401
from app.modules.reports import models as reports_models  # noqa: F401
