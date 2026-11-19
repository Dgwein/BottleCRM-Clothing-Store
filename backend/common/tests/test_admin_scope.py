"""The Django admin works in one organization or the whole instance, chosen
on sign-in (common/admin_site.py, common/admin_scope.py,
common/admin_mixins.py)."""

import pytest
from django.contrib import admin
from django.db import connection

from accounts.models import Account
from common import admin_scope
from common.admin_mixins import OrgScopedAdmin, _is_org_scoped
from common.audit_log import SecurityAuditLog
from common.models import Org, Profile, User
from contacts.models import Contact
from crm.db_router import AdminInstanceRouter
from invoices.models import Product
from orders.models import Order

SCOPE_URL = "/admin/scope/"


def choose(client, value):
    session = client.session
    session[admin_scope.SESSION_KEY] = value
    session.save()


@pytest.fixture
def superuser(db):
    return User.objects.create_superuser(email="root@test.com", password="x")


@pytest.fixture
def root(client, superuser):
    client.force_login(superuser)
    return client


def staff(email):
    user = User.objects.create_user(email=email, password="x")
    user.is_staff = True
    user.is_superuser = False
    user.save()
    return user


def found(response):
    assert response.status_code == 200, response.status_code
    return response.context["cl"].result_count


# ---------------------------------------------------------------------------
# Choosing
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestChoosing:
    def test_staff_without_a_scope_is_sent_to_choose(self, root):
        response = root.get("/admin/orders/order/")

        assert response.status_code == 302
        assert response.url == f"{SCOPE_URL}?next=/admin/orders/order/"

    def test_the_chooser_lists_active_orgs_and_the_instance(self, root, org_a, org_b):
        Org.objects.create(name="Closed", is_active=False)

        response = root.get(SCOPE_URL)

        assert response.status_code == 200
        assert list(response.context["orgs"]) == sorted(
            [org_a, org_b], key=lambda o: o.name
        )
        assert response.context["may_use_instance"] is True
        assert b"Whole instance (every organization)" in response.content

    def test_choosing_an_org_stores_audits_and_goes_on(self, root, org_a, superuser):
        response = root.post(
            SCOPE_URL, {"scope": str(org_a.pk), "next": "/admin/orders/order/"}
        )

        assert response.status_code == 302
        assert response.url == "/admin/orders/order/"
        assert root.session[admin_scope.SESSION_KEY] == str(org_a.pk)
        entry = SecurityAuditLog.objects.get(event_type="ORG_SWITCH")
        assert (entry.user, entry.org) == (superuser, org_a)
        assert entry.metadata["admin"] is True
        assert entry.description == f"Admin switched from none to {org_a.name}"

    def test_choosing_the_instance_is_audited_too(self, root, org_a):
        choose(root, str(org_a.pk))

        root.post(SCOPE_URL, {"scope": "instance"})

        assert root.session[admin_scope.SESSION_KEY] == "instance"
        entry = SecurityAuditLog.objects.get(event_type="ORG_SWITCH")
        assert entry.org is None
        assert entry.metadata["instance"] is True
        assert entry.description == (
            f"Admin switched from {org_a.name} to the whole instance"
        )

    def test_the_header_and_index_show_the_scope(self, root, org_a):
        choose(root, str(org_a.pk))

        response = root.get("/admin/")

        assert f"Organization: {org_a.name}".encode() in response.content
        assert b'href="/admin/scope/">Switch</a>' in response.content

    @pytest.mark.parametrize(
        "next_url", ["https://evil.example/", "//evil.example/", "/api/leads/"]
    )
    def test_next_outside_the_admin_is_ignored(self, root, org_a, next_url):
        response = root.post(SCOPE_URL, {"scope": str(org_a.pk), "next": next_url})

        assert response.url == "/admin/"

    @pytest.mark.parametrize(
        "value", ["", "nope", "6f1c2d3e-4a5b-4c6d-8e7f-901a2b3c4d5e"]
    )
    def test_an_unknown_choice_is_refused(self, root, org_a, value):
        response = root.post(SCOPE_URL, {"scope": value})

        assert response.status_code == 400
        assert b"Choose one of the organizations listed." in response.content
        assert admin_scope.SESSION_KEY not in root.session

    def test_sign_out_needs_no_scope(self, root):
        response = root.post("/admin/logout/")

        assert response.status_code == 200

    def test_signed_out_users_go_to_sign_in_not_to_choose(self, client):
        response = client.get("/admin/orders/order/")

        assert response.status_code == 302
        assert response.url.startswith("/admin/login/")


# ---------------------------------------------------------------------------
# Who may choose what
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWhoMayChoose:
    def test_staff_may_choose_only_orgs_they_administer(self, client, org_a, org_b):
        user = staff("s@test.com")
        Profile.objects.create(user=user, org=org_a, role="ADMIN")
        Profile.objects.create(user=user, org=org_b, role="USER")
        client.force_login(user)

        page = client.get(SCOPE_URL)
        refused = client.post(SCOPE_URL, {"scope": str(org_b.pk)})
        allowed = client.post(SCOPE_URL, {"scope": str(org_a.pk)})

        assert list(page.context["orgs"]) == [org_a]
        assert page.context["instance_offered"] is False
        assert refused.status_code == 400
        assert allowed.status_code == 302

    def test_staff_may_not_choose_the_instance(self, client, org_a):
        user = staff("s@test.com")
        Profile.objects.create(user=user, org=org_a, role="ADMIN")
        client.force_login(user)

        response = client.post(SCOPE_URL, {"scope": "instance"})

        assert response.status_code == 403
        assert admin_scope.SESSION_KEY not in client.session

    def test_an_inactive_membership_does_not_count(self, client, org_a):
        user = staff("s@test.com")
        Profile.objects.create(user=user, org=org_a, role="ADMIN", is_active=False)
        client.force_login(user)

        assert list(client.get(SCOPE_URL).context["orgs"]) == []

    def test_a_revoked_membership_drops_the_scope(self, client, org_a):
        user = staff("s@test.com")
        profile = Profile.objects.create(user=user, org=org_a, role="ADMIN")
        client.force_login(user)
        choose(client, str(org_a.pk))
        profile.role = "USER"
        profile.save()

        response = client.get("/admin/")

        assert response.status_code == 302
        assert response.url.startswith(SCOPE_URL)

    def test_a_deactivated_org_drops_the_scope(self, root, org_a):
        choose(root, str(org_a.pk))
        Org.objects.filter(pk=org_a.pk).update(is_active=False)

        assert root.get("/admin/").status_code == 302

    def test_a_stored_instance_choice_is_not_honoured_for_staff(self, client, org_a):
        user = staff("s@test.com")
        Profile.objects.create(user=user, org=org_a, role="ADMIN")
        client.force_login(user)
        choose(client, "instance")

        assert client.get("/admin/").status_code == 302


# ---------------------------------------------------------------------------
# What each mode shows and allows
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOrganizationMode:
    @pytest.fixture
    def rows(self, org_a, org_b):
        for org in (org_a, org_b):
            account = Account.objects.create(org=org, name=f"Account {org.name}")
            Order.objects.create(org=org, account=account, name=f"Order {org.name}")
            Product.objects.create(org=org, name=f"Product {org.name}", price=1)
            Contact.objects.create(org=org, first_name="C", last_name=org.name)

    @pytest.mark.parametrize(
        "url",
        [
            "/admin/orders/order/",
            "/admin/invoices/product/",
            "/admin/contacts/contact/",
        ],
    )
    def test_lists_only_the_orgs_rows(self, root, org_a, rows, url):
        choose(root, str(org_a.pk))

        response = root.get(url)

        assert found(response) == 1
        assert all(row.org_id == org_a.pk for row in response.context["cl"].result_list)

    def test_another_orgs_record_does_not_open(self, root, org_a, org_b, rows):
        choose(root, str(org_a.pk))
        theirs = Order.objects.get(org=org_b)

        response = root.get(f"/admin/orders/order/{theirs.pk}/change/")

        assert response.status_code == 302
        assert response.url == "/admin/"

    def test_pickers_offer_only_the_orgs_rows(self, root, org_a, org_b, rows):
        choose(root, str(org_a.pk))

        form = root.get("/admin/orders/order/add/").context["adminform"].form

        assert list(form.fields["org"].queryset) == [org_a]
        assert set(form.fields["account"].queryset) == set(
            Account.objects.filter(org=org_a)
        )
        assert form.initial["org"] == org_a.pk

    def test_many_to_many_pickers_offer_only_the_orgs_rows(
        self, root, org_a, org_b, superuser
    ):
        mine = Profile.objects.create(user=superuser, org=org_a)
        Profile.objects.create(
            user=User.objects.create_user(email="o@test.com", password="x"),
            org=org_b,
        )
        choose(root, str(org_a.pk))

        form = root.get("/admin/tasks/task/add/").context["adminform"].form

        assert list(form.fields["assigned_to"].queryset) == [mine]

    def test_a_new_record_can_not_be_put_in_another_org(self, root, org_a, org_b):
        choose(root, str(org_a.pk))

        response = root.post(
            "/admin/invoices/product/add/",
            {"name": "Planted", "price": "1", "org": str(org_b.pk), "is_active": "on"},
        )

        assert response.status_code == 200
        assert "org" in response.context["adminform"].form.errors
        assert not Product.objects.filter(name="Planted").exists()

    def test_a_new_record_is_made_in_the_org(self, root, org_a):
        choose(root, str(org_a.pk))

        response = root.post(
            "/admin/invoices/product/add/",
            {
                "name": "Mine",
                "price": "1",
                "size": "Unique",
                "stock": "0",
                "org": str(org_a.pk),
                "is_active": "on",
            },
        )

        assert response.status_code == 302
        assert Product.objects.get(name="Mine").org == org_a

    def test_the_org_filter_is_hidden(self, root, org_a, rows):
        choose(root, str(org_a.pk))

        response = root.get("/admin/orders/order/")

        titles = [spec.title for spec in response.context["cl"].filter_specs]
        assert "org" not in titles

    def test_the_user_list_shows_the_orgs_members(self, root, org_a, org_b, superuser):
        member = User.objects.create_user(email="m@test.com", password="x")
        other = User.objects.create_user(email="o@test.com", password="x")
        Profile.objects.create(user=member, org=org_a)
        Profile.objects.create(user=other, org=org_b)
        choose(root, str(org_a.pk))

        listed = set(root.get("/admin/common/user/").context["cl"].result_list)

        assert listed == {member}

    def test_save_model_puts_a_new_object_in_the_org(self, org_a, rf):
        request = rf.post("/admin/")
        request.admin_scope = admin_scope.Scope(org_a)
        product = Product(name="x", price=1)

        admin.site._registry[Product].save_model(request, product, None, False)

        assert Product.objects.get(pk=product.pk).org == org_a

    def test_save_model_refuses_an_object_of_another_org(self, org_a, org_b, rf):
        from django.core.exceptions import PermissionDenied

        request = rf.post("/admin/")
        request.admin_scope = admin_scope.Scope(org_a)
        product = Product(org=org_b, name="x", price=1)

        with pytest.raises(PermissionDenied):
            admin.site._registry[Product].save_model(request, product, None, False)


@pytest.mark.django_db
class TestInstanceMode:
    def test_lists_every_orgs_rows(self, root, org_a, org_b):
        for org in (org_a, org_b):
            Product.objects.create(org=org, name=f"P {org.name}", price=1)
        choose(root, "instance")

        response = root.get("/admin/invoices/product/")

        assert found(response) == 2
        assert b"Whole instance" in response.content

    def test_all_users_are_listed(self, root, org_a, superuser):
        member = User.objects.create_user(email="m@test.com", password="x")
        Profile.objects.create(user=member, org=org_a)
        choose(root, "instance")

        listed = set(root.get("/admin/common/user/").context["cl"].result_list)

        assert {member, superuser} <= listed


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def _admins():
    for model, model_admin in admin.site._registry.items():
        yield model, model_admin
        for inline in model_admin.inlines:
            yield inline.model, inline


def test_every_org_scoped_admin_is_scoped():
    """A new admin for an org-scoped model must use OrgScopedAdmin, or it
    would list every org's rows in organization mode."""
    unscoped = sorted(
        f"{model._meta.label} ({getattr(owner, '__name__', type(owner).__name__)})"
        for model, owner in _admins()
        if _is_org_scoped(model)
        and not isinstance(owner, OrgScopedAdmin)
        and not (isinstance(owner, type) and issubclass(owner, OrgScopedAdmin))
    )
    assert unscoped == []


def test_the_guard_finds_admins():
    assert sum(1 for model, _ in _admins() if _is_org_scoped(model)) >= 30


def test_the_router_sends_only_instance_requests_to_the_instance_db():
    router = AdminInstanceRouter()

    assert router.db_for_read(Order) is None
    assert router.db_for_write(Order) is None
    token = admin_scope.use_instance_db.set(True)
    try:
        assert router.db_for_read(Order) == "admin_instance"
        assert router.db_for_write(Order) == "admin_instance"
    finally:
        admin_scope.use_instance_db.reset(token)
    assert router.allow_relation(Order(), Order()) is True
    assert router.allow_migrate("default", "orders") is True
    assert router.allow_migrate("admin_instance", "orders") is False


@pytest.mark.django_db
def test_activate_restores_the_enclosing_scope(org_a, org_b):
    with admin_scope.activate(admin_scope.Scope(None)):
        assert admin_scope.use_instance_db.get() is True
        with admin_scope.activate(admin_scope.Scope(org_b)):
            assert admin_scope._active_org_id.get() == org_b.pk
            assert admin_scope.use_instance_db.get() is False
        assert admin_scope._active_org_id.get() is None
        assert admin_scope.use_instance_db.get() is True
    assert admin_scope.use_instance_db.get() is False


# ---------------------------------------------------------------------------
# PostgreSQL, as a role row-level security binds
# ---------------------------------------------------------------------------


def _need_rls():
    if connection.vendor != "postgresql":
        pytest.skip("Row-level security is a PostgreSQL feature")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        if cursor.fetchone()[0]:
            pytest.skip("Needs a role row-level security binds, as in CI")


@pytest.mark.postgres_only
def test_organization_mode_sees_the_orgs_rows_under_rls(client, org_a, org_b):
    """Before the scope, an RLS-bound admin listed no org-scoped row at all."""
    _need_rls()
    from conftest import clear_rls_context, rls_org

    for org in (org_a, org_b):
        with rls_org(org):
            account = Account.objects.create(org=org, name=f"A {org.name}")
            Order.objects.create(org=org, account=account, name=f"O {org.name}")
    client.force_login(User.objects.create_superuser(email="r@test.com", password="x"))
    choose(client, str(org_a.pk))
    clear_rls_context()

    response = client.get("/admin/orders/order/")

    assert [o.name for o in response.context["cl"].result_list] == [f"O {org_a.name}"]


@pytest.mark.postgres_only
def test_instance_mode_is_not_offered_without_an_instance_connection(client, org_a):
    _need_rls()
    admin_scope._role_bypasses_rls.cache_clear()
    client.force_login(User.objects.create_superuser(email="r@test.com", password="x"))

    refused = client.post(SCOPE_URL, {"scope": "instance"})
    page = client.get(SCOPE_URL)

    assert refused.status_code == 403
    assert page.context["may_use_instance"] is False
    assert b"(ADMIN_DBUSER) is configured" in page.content
