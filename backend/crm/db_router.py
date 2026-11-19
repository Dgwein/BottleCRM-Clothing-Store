"""Send the Django admin's whole-instance requests to the `admin_instance`
connection, whose role bypasses row-level security. Installed only when
ADMIN_DBUSER is set (crm/settings.py); everything else uses `default`."""

from common.admin_scope import INSTANCE_DB_ALIAS, use_instance_db


class AdminInstanceRouter:
    def db_for_read(self, model, **hints):
        return INSTANCE_DB_ALIAS if use_instance_db.get() else None

    def db_for_write(self, model, **hints):
        return INSTANCE_DB_ALIAS if use_instance_db.get() else None

    def allow_relation(self, obj1, obj2, **hints):
        # Both aliases are the same database.
        return True

    def allow_migrate(self, db, app_label, **hints):
        return db != INSTANCE_DB_ALIAS
