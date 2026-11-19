from django.contrib import admin, messages
from django.contrib.admin.utils import unquote
from django.http import HttpResponseRedirect

from common.admin_mixins import OrgScopedAdmin
from common.admin_scope import admin_org
from common.models import Address, Comment, CommentFiles, Org, User
from common.org_deletion import delete_org, describe, org_deals, org_sales
from common.tasks import clear_rls_context, set_rls_context


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        # Users belong to the instance; in organization mode, list the org's.
        queryset = super().get_queryset(request)
        org = admin_org(request)
        if org is None:
            return queryset
        return queryset.filter(profiles__org=org).distinct()


@admin.register(Address)
class AddressAdmin(OrgScopedAdmin, admin.ModelAdmin):
    pass


@admin.register(Comment)
class CommentAdmin(OrgScopedAdmin, admin.ModelAdmin):
    pass


@admin.register(CommentFiles)
class CommentFilesAdmin(OrgScopedAdmin, admin.ModelAdmin):
    pass


@admin.register(Org)
class OrgAdmin(admin.ModelAdmin):
    """Organizations are read here, and deleted by a superuser only, one at a
    time. The delete page names the org's sales (invoices, orders, recurring
    invoices) and its deals, says that nothing can be undone, and deletes only
    once each of them has its box ticked."""

    list_display = ("name", "id", "is_active", "created_at")
    search_fields = ("name",)
    delete_confirmation_template = "admin/common/org/delete_confirmation.html"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        user = request.user
        return bool(user.is_active and user.is_superuser)

    def get_actions(self, request):
        # One org at a time, through the page that names its sales and deals.
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def get_deleted_objects(self, objs, request):
        deleted, counts, perms_needed, _protected = super().get_deleted_objects(
            objs, request
        )
        return deleted, counts, perms_needed, []

    def delete_view(self, request, object_id, extra_context=None):
        org = self.get_object(request, unquote(object_id))
        if org is None or not self.has_delete_permission(request, org):
            return super().delete_view(request, object_id, extra_context)
        set_rls_context(org.pk)
        try:
            sales, deals = org_sales(org), org_deals(org)
            if request.method == "POST":
                missing = [
                    found
                    for found, box in ((sales, "delete_sales"), (deals, "delete_deals"))
                    if found and request.POST.get(box) != "yes"
                ]
                if missing:
                    self.message_user(
                        request,
                        "Nothing was deleted. Tick each box to confirm that "
                        f"{' and '.join(describe(m) for m in missing)} are deleted "
                        "too.",
                        messages.ERROR,
                    )
                    return HttpResponseRedirect(request.path)
            context = {
                **(extra_context or {}),
                "sales": describe(sales) if sales else "",
                "deals": describe(deals) if deals else "",
            }
            return super().delete_view(request, object_id, context)
        finally:
            clear_rls_context()

    def delete_model(self, request, obj):
        delete_org(
            obj,
            user=request.user,
            with_sales=request.POST.get("delete_sales") == "yes",
            with_deals=request.POST.get("delete_deals") == "yes",
            request=request,
        )
