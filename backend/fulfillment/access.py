"""Who may see and work each kind of fulfilment order.

Order handling belongs to the teams assigned to it: a ``QueueAssignment`` gives
a team ``VIEW``, ``HANDLE`` or (delivery only) ``CLOSE`` on one order type,
and a member has the highest level any of their teams holds. Org admins (and
app members, which are admins for records) hold every level.
"""

from common.permissions import is_org_admin
from fulfillment.models import ACCESS_LEVELS, ORDER_TYPES, QueueAssignment

RANK = {level: rank for rank, (level, _) in enumerate(ACCESS_LEVELS, start=1)}
LABELS = dict(ORDER_TYPES)


def top_level(order_type):
    return "CLOSE" if order_type == "DELIVERY" else "HANDLE"


def levels(profile):
    """``{order_type: level}`` for every order type ``profile`` may at least view."""
    if is_org_admin(profile):
        return {t: top_level(t) for t, _ in ORDER_TYPES}
    held = {}
    rows = QueueAssignment.objects.filter(
        org=profile.org, team__users=profile
    ).values_list("order_type", "access")
    for order_type, access in rows:
        if RANK[access] > RANK.get(held.get(order_type), 0):
            held[order_type] = access
    return held


def may(profile, order_type, level):
    held = levels(profile).get(order_type)
    return held is not None and RANK[held] >= RANK[level]


def refusal(order_type, level):
    verb = {"VIEW": "see", "HANDLE": "work", "CLOSE": "close"}[level]
    noun = "dispatch" if order_type == "DELIVERY" else LABELS[order_type].lower()
    return f"None of your teams may {verb} {noun} orders."
