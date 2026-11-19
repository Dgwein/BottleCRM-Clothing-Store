"""The Django admin site, scoped to one organization or the whole instance.

`CRMAdminConfig` replaces `django.contrib.admin` in INSTALLED_APPS, which makes
`CRMAdminSite` the default `admin.site` every app registers with. Only paths
under /admin/ are affected: the API, the web and mobile apps, Celery and
management commands never go through it. The rules are in
`common.admin_scope`.
"""

import uuid
from functools import wraps

from django.contrib import admin
from django.contrib.admin.apps import AdminConfig
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.http import url_has_allowed_host_and_scheme

# Admin views that work without a chosen scope.
_UNSCOPED_VIEWS = frozenset(
    {
        "logout",
        "scope_view",
        "password_change",
        "password_change_done",
        "i18n_javascript",
    }
)


# This module is imported while the app registry loads (CRMAdminConfig), so it
# imports nothing that touches models at module level.


class CRMAdminSite(admin.AdminSite):
    index_template = "admin/crm/index.html"

    def get_urls(self):
        return [
            path("scope/", self.admin_view(self.scope_view), name="scope"),
            *super().get_urls(),
        ]

    def admin_view(self, view, cacheable=False):
        inner = super().admin_view(view, cacheable)
        name = getattr(view, "__name__", "")

        @wraps(inner)
        def scoped(request, *args, **kwargs):
            from common import admin_scope

            if not self.has_permission(request):
                return inner(request, *args, **kwargs)
            scope = admin_scope.current(request)
            request.admin_scope = scope
            if scope is None:
                if name in _UNSCOPED_VIEWS:
                    return inner(request, *args, **kwargs)
                return HttpResponseRedirect(
                    f"{reverse('admin:scope', current_app=self.name)}?next="
                    f"{request.get_full_path()}"
                )
            with admin_scope.activate(scope):
                response = inner(request, *args, **kwargs)
                # Admin pages render after the view returns; the rows they
                # list must be read in the scope, so render here.
                if hasattr(response, "render") and not response.is_rendered:
                    response.render()
                return response

        return scoped

    def each_context(self, request):
        context = super().each_context(request)
        scope = getattr(request, "admin_scope", None)
        context["admin_scope"] = scope
        if scope is not None:
            context["site_header"] = f"{self.site_header} | {scope.label}"
        return context

    def scope_view(self, request):
        """Choose the organization to work in, or the whole instance."""
        from common import admin_scope

        orgs = admin_scope.allowed_orgs(request.user)
        may_use_instance = admin_scope.may_use_instance(request.user)
        next_url = request.POST.get("next") or request.GET.get("next") or ""
        if not url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ) or not next_url.startswith(reverse("admin:index", current_app=self.name)):
            next_url = reverse("admin:index", current_app=self.name)
        error = None
        if request.method == "POST":
            choice = request.POST.get("scope", "")
            if choice == admin_scope.INSTANCE:
                if not may_use_instance:
                    raise PermissionDenied
                admin_scope.choose(request, None)
                return HttpResponseRedirect(next_url)
            org = orgs.filter(pk=choice).first() if _is_uuid(choice) else None
            if org is None:
                error = "Choose one of the organizations listed."
            else:
                admin_scope.choose(request, org)
                return HttpResponseRedirect(next_url)
        request.admin_scope = admin_scope.current(request)
        context = {
            **self.each_context(request),
            "title": "Choose where to work",
            "orgs": orgs,
            "may_use_instance": may_use_instance,
            "instance_offered": request.user.is_superuser,
            "current": request.admin_scope,
            "next": next_url,
            "error": error,
        }
        return TemplateResponse(
            request, "admin/crm/scope.html", context, status=400 if error else 200
        )


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


class CRMAdminConfig(AdminConfig):
    default_site = "common.admin_site.CRMAdminSite"
