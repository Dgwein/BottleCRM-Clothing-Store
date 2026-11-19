"""Which organization, if any, a Django admin session works in.

A staff user chooses on sign-in (see `common.admin_site`): one organization,
or the whole instance. In organization mode every admin request runs with that
org's RLS context and `OrgScopedAdmin` filters every org-scoped admin by it.
Instance mode shows every org; on a database role that row-level security
binds, it needs the `admin_instance` connection (`ADMIN_DBUSER`), whose role
bypasses RLS, and is offered only when that is configured.

Who may choose what: a superuser, any active org and the instance; any other
staff user, only active orgs where they hold an active ADMIN profile.
"""

import contextlib
import functools
from contextvars import ContextVar
from dataclasses import dataclass

from django.conf import settings
from django.db import connection

from common.audit_log import audit_log
from common.models import Org
from common.tasks import clear_rls_context, set_rls_context

SESSION_KEY = "admin_scope"
INSTANCE = "instance"
# The alias `crm.db_router.AdminInstanceRouter` sends instance-mode queries to.
INSTANCE_DB_ALIAS = "admin_instance"

_active_org_id: ContextVar = ContextVar("admin_active_org_id", default=None)
use_instance_db: ContextVar = ContextVar("admin_use_instance_db", default=False)


@dataclass(frozen=True)
class Scope:
    org: Org | None  # None is the whole instance

    @property
    def label(self) -> str:
        return f"Organization: {self.org.name}" if self.org else "Whole instance"


def allowed_orgs(user):
    orgs = Org.objects.filter(is_active=True).order_by("name")
    if user.is_superuser:
        return orgs
    return orgs.filter(
        profiles__user=user, profiles__is_active=True, profiles__role="ADMIN"
    ).distinct()


@functools.lru_cache(maxsize=1)
def _role_bypasses_rls() -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        row = cursor.fetchone()
    return bool(row and row[0])


def instance_mode_available() -> bool:
    """Whether instance mode can show every org's rows on this database."""
    if connection.vendor != "postgresql":
        return True
    return INSTANCE_DB_ALIAS in settings.DATABASES or _role_bypasses_rls()


def may_use_instance(user) -> bool:
    return user.is_superuser and instance_mode_available()


def current(request) -> Scope | None:
    """The session's scope, checked again against what the user may choose
    now: a revoked membership or a deactivated org drops it."""
    stored = request.session.get(SESSION_KEY)
    if stored == INSTANCE:
        return Scope(None) if may_use_instance(request.user) else None
    if stored:
        org = allowed_orgs(request.user).filter(pk=stored).first()
        if org is not None:
            return Scope(org)
    return None


def choose(request, org: Org | None) -> None:
    """Store the user's choice (already checked) and audit it."""
    previous = current(request)
    request.session[SESSION_KEY] = str(org.pk) if org else INSTANCE
    audit_log.admin_scope_changed(
        request.user,
        previous.org if previous else None,
        org,
        from_instance=bool(previous and previous.org is None),
        request=request,
    )


def admin_org(request) -> Org | None:
    """The org the request is scoped to; None in instance mode or outside a
    scoped admin request."""
    scope = getattr(request, "admin_scope", None)
    return scope.org if scope else None


@contextlib.contextmanager
def activate(scope: Scope):
    """Run a block in a scope: the org's RLS context, or the instance
    connection. Restores the enclosing scope on the way out, so an admin view
    can act on a chosen org inside the session's own scope."""
    org_token = _active_org_id.set(scope.org.pk if scope.org else None)
    db_token = use_instance_db.set(scope.org is None)
    _apply(scope.org.pk if scope.org else None)
    try:
        yield
    finally:
        _active_org_id.reset(org_token)
        use_instance_db.reset(db_token)
        _apply(_active_org_id.get())


def _apply(org_id) -> None:
    if org_id:
        set_rls_context(org_id)
    else:
        clear_rls_context()
