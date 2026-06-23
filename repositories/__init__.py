"""Edge-side repositories.

These hide the difference between "online with the PostgreSQL master" and
"offline-only with SQLite cache". Call sites never branch on which mode is
active — they just use the repo methods and the implementation degrades
gracefully if PostgreSQL is disabled or unreachable.
"""

from .classes import ClassRepository
from .devices import DeviceRepository
from .dietary import DietaryRestrictionRepository
from .events import PresenceEventRepository
from .kitchen import KitchenRepository
from .postgres_client import cursor, get_connection, is_cloud_enabled
from .students import StudentRepository

__all__ = [
    "ClassRepository",
    "DeviceRepository",
    "DietaryRestrictionRepository",
    "KitchenRepository",
    "PresenceEventRepository",
    "StudentRepository",
    "cursor",
    "get_connection",
    "is_cloud_enabled",
]
