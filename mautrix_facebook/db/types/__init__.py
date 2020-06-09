from datetime import timezone

import sqlalchemy.types as types


class UTCDateTime(types.TypeDecorator):
    """Decorates the SQLAlchemy DateTime type to work with UTC datetimes.

    It supposes we only manipulate UTC datetime. If the timezone is not set when saving or reading
    a value, the UTC timezone is set. If a timezone is set, it ensures the datetime is converted to
    UTC before saving it.
    This is useful when working with SQLite as the SQLalchemy DateTime type loses the timezone
    information when saving a datetime on this database.
    """
    impl = types.DateTime

    def process_bind_param(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            elif value.tzinfo != timezone.utc:
                value = value.astimezone(timezone.utc)

        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        else:
            return value
