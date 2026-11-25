"""Importa todos los modelos de SQLAlchemy."""

from app.events import models as events_models  # noqa: F401
from app.modules.alerts import models as alerts_models  # noqa: F401
from app.modules.catalog import models as catalog_models  # noqa: F401
from app.modules.inventory import models as inventory_models  # noqa: F401
from app.modules.products import models as products_models  # noqa: F401
from app.modules.reports import models as reports_models  # noqa: F401
from app.modules.requests import models as requests_models  # noqa: F401
from app.modules.transfers import models as transfers_models  # noqa: F401
from app import security  # noqa: F401
