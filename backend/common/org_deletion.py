"""Deleting an organization.

An org with sales (invoices, orders or recurring invoices) is not deleted:
those are the books, and deleting the org deletes them with it. Only a
superuser may delete such an org, sales included, through
`delete_org(..., with_sales=True)`. An org with deals is deleted only once
whoever deletes it has agreed that the deals go too (`with_deals=True`).
The Django admin names both and asks for a tick for each before anything
happens; neither can be undone.

`delete_org` checks all this first. A `pre_delete` receiver refuses an org
with sales on every other path too (a shell, a queryset delete). Deals are a
question for the person deleting, so only `delete_org` asks it; code that
deletes an org directly has nobody to ask. See
docs/architecture/catalog-distro-changes.md.
"""

from contextvars import ContextVar

from django.apps import apps
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from common.audit_log import audit_log
from common.models import Org
from common.tasks import clear_rls_context, set_rls_context

# (label, plural, model): the records that make an org's sales.
SALES = (
    ("invoice", "invoices", "invoices.Invoice"),
    ("order", "orders", "orders.Order"),
    ("recurring invoice", "recurring invoices", "invoices.RecurringInvoice"),
)

# The orgs `delete_org` has checked and cleared, for the receiver below.
_cleared = ContextVar("org_deletion_cleared", default=frozenset())

# Rows that PROTECT something the org's cascade deletes (an account, a
# profile, a sales channel). Django refuses the whole delete while any is
# left, even though what they protect goes too, so they are deleted first.
_PROTECTING = (
    "cases.TimeEntry",
    "invoices.Invoice",
    "invoices.Estimate",
    "invoices.RecurringInvoice",
    "orders.Order",
    "fulfillment.DispatchOrder",
    "fulfillment.DeliveryOption",
    "projects.Project",
)


class OrgHasSales(Exception):
    """Raised, with a sentence safe to show, when an org with sales would be
    deleted without a superuser's confirmation."""


class OrgHasDeals(Exception):
    """Raised, with a sentence safe to show, when an org with deals would be
    deleted before anyone agreed that its deals go too."""


def _counted(rows):
    return [(single if n == 1 else plural, n) for single, plural, n in rows if n]


def org_sales(org):
    """`[(label, count)]` of the org's sales records, only those it has."""
    return _counted(
        (single, plural, apps.get_model(path).objects.filter(org=org).count())
        for single, plural, path in SALES
    )


def org_deals(org):
    """`[(label, count)]` of the org's deals, empty when it has none."""
    count = apps.get_model("opportunity.Opportunity").objects.filter(org=org).count()
    return _counted([("deal", "deals", count)])


def describe(found):
    parts = [f"{count} {label}" for label, count in found]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _sales_refusal(org, sales):
    return (
        f"{org.name} has {describe(sales)}, so it cannot be deleted. Only a "
        "superuser can delete it with its sales, from the Django admin."
    )


def _deals_refusal(org, deals):
    return (
        f"{org.name} has {describe(deals)}, deleted with it and not recoverable. "
        "Agree to that first: the tick in the Django admin, or with_deals=True."
    )


@receiver(pre_delete, sender=Org)
def _refuse_unchecked(sender, instance, **kwargs):
    if instance.pk in _cleared.get():
        return
    if sales := org_sales(instance):
        raise OrgHasSales(_sales_refusal(instance, sales))


def delete_org(org, *, user, with_sales=False, with_deals=False, request=None):
    """Delete `org` and everything it holds, and record who did it.

    Refused, with nothing touched: an org with sales unless `with_sales`
    (`OrgHasSales`), which only an active superuser may pass; an org with
    deals unless `with_deals` (`OrgHasDeals`). One transaction, under the
    org's RLS context, so every row it holds is seen and removed under a
    non-superuser database role."""
    if with_sales and not (user and user.is_active and user.is_superuser):
        raise PermissionDenied("Only a superuser can delete an organization's sales.")
    org_id, name = org.pk, org.name
    set_rls_context(org_id)
    token = _cleared.set(_cleared.get() | {org_id})
    try:
        with transaction.atomic():
            sales, deals = org_sales(org), org_deals(org)
            if sales and not with_sales:
                raise OrgHasSales(_sales_refusal(org, sales))
            if deals and not with_deals:
                raise OrgHasDeals(_deals_refusal(org, deals))
            for model_path in _PROTECTING:
                apps.get_model(model_path).objects.filter(org=org).delete()
            org.delete()
    finally:
        _cleared.reset(token)
        clear_rls_context()
    audit_log.org_deleted(user, org_id, name, sales, deals, request=request)
    return sales, deals
