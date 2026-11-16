"""پکیج دیتابیس — کلاس Database از mixinها."""

from ..config import config

from .channels import ChannelsMixin
from .core import CoreMixin
from .posts import PostsMixin
from .settings import SettingsMixin
from .users import UsersMixin


class Database(
    CoreMixin,
    SettingsMixin,
    UsersMixin,
    ChannelsMixin,
    PostsMixin,
):
    pass


db = Database(config.DB_PATH)
