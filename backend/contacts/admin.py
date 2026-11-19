from django.contrib import admin

from common.admin_mixins import OrgScopedAdmin
from contacts.models import Contact


@admin.register(Contact)
class ContactAdmin(OrgScopedAdmin, admin.ModelAdmin):
    pass
