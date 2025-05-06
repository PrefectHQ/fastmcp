import datetime
from typing import Any

UTC = datetime.timezone.utc


class TimedCache:
    NOT_FOUND = object()

    def __init__(self, expiration: datetime.timedelta):
        self.expiration = expiration
        self.cache: dict[Any, tuple[Any, datetime.datetime]] = {}

    def set(self, key: Any, value: Any) -> None:
        expires = datetime.datetime.now(UTC) + self.expiration
        self.cache[key] = (value, expires)

    def get(self, key: Any) -> Any:
        value = self.cache.get(key)
        if value is not None and value[1] > datetime.datetime.now(UTC):
            return value[0]
        else:
            return self.NOT_FOUND

    def clear(self) -> None:
        self.cache.clear()
