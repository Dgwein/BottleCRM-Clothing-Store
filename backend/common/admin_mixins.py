"""`OrgScopedAdmin`: an admin (or inline) for an org-scoped model, held to the
organization the admin session works in (`common.admin_scope`).

In organization mode it lists and opens only that org's rows, offers only its
rows in foreign-key and many-to-many pickers, fills in `org` on a new row and
refuses to save a row of another org. RLS enforces the same underneath on a
role it binds; this is the ORM half, which also holds on SQLite. In instance
mode, or outside a scoped admin request, it changes nothing.
"""

from django.core.exceptions import FieldDoesNotExist, PermissionDenied

from common.admin_scope import admin_org
from common.models import Org


def _is_org_scoped(model) -> bool:
    try:
        field = model._meta.get_field("org")
    except FieldDoesNotExist:
        return False
    return getattr(field, "related_model", None) is Org


def _scoped_queryset(model, org):
    if model is Org:
        return Org.objects.filter(pk=org.pk)
    if _is_org_scoped(model):
        return model._default_manager.filter(org=org)
    return None


class OrgScopedAdmin:
    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        org = admin_org(request)
        return queryset.filter(org=org) if org is not None else queryset

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        org = admin_org(request)
        if org is not None:
            scoped = _scoped_queryset(db_field.related_model, org)
            if scoped is not None:
                kwargs["queryset"] = scoped
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        org = admin_org(request)
        if org is not None:
            scoped = _scoped_queryset(db_field.related_model, org)
            if scoped is not None:
                kwargs["queryset"] = scoped
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def get_list_filter(self, request):
        filters = super().get_list_filter(request)
        if admin_org(request) is not None:
            return [f for f in filters if f != "org"]
        return filters

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        org = admin_org(request)
        if org is not None:
            initial.setdefault("org", org.pk)
        return initial

    def save_model(self, request, obj, form, change):
        org = admin_org(request)
        if org is not None:
            if obj.org_id is None:
                obj.org = org
            elif obj.org_id != org.pk:
                raise PermissionDenied
        super().save_model(request, obj, form, change)
