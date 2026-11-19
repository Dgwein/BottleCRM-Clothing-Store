from django.contrib import admin

from common.admin_mixins import OrgScopedAdmin
from common.admin_scope import admin_org
from common.models import Address, Comment, CommentFiles, User


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
