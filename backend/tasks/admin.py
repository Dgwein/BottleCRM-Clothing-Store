from django.contrib import admin

from common.admin_mixins import OrgScopedAdmin
from tasks.models import Board, BoardColumn, BoardMember, BoardTask, Task


@admin.register(Task)
class TaskAdmin(OrgScopedAdmin, admin.ModelAdmin):
    pass


@admin.register(Board)
class BoardAdmin(OrgScopedAdmin, admin.ModelAdmin):
    list_display = ("name", "owner", "org", "is_archived", "created_at")
    list_filter = ("is_archived", "org", "created_at")
    search_fields = ("name", "owner__user__email")
    raw_id_fields = ("owner", "org")


@admin.register(BoardMember)
class BoardMemberAdmin(OrgScopedAdmin, admin.ModelAdmin):
    list_display = ("board", "profile", "role", "created_at")
    list_filter = ("role", "created_at")
    search_fields = ("board__name", "profile__user__email")
    raw_id_fields = ("board", "profile")


@admin.register(BoardColumn)
class BoardColumnAdmin(OrgScopedAdmin, admin.ModelAdmin):
    list_display = ("board", "name", "order", "color", "limit")
    list_filter = ("board__org", "created_at")
    search_fields = ("name", "board__name")
    raw_id_fields = ("board",)
    ordering = ("board", "order")


@admin.register(BoardTask)
class BoardTaskAdmin(OrgScopedAdmin, admin.ModelAdmin):
    list_display = (
        "title",
        "column",
        "priority",
        "due_date",
        "is_completed",
        "created_at",
    )
    list_filter = ("priority", "completed_at", "created_at")
    search_fields = ("title", "description")
    raw_id_fields = ("column", "account", "contact", "opportunity")
    filter_horizontal = ("assigned_to",)
