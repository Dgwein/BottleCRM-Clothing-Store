"""Seed an org for the catalog browser tests and print what they need as JSON.

Run from backend/ against the database the running backend uses:

    uv run python manage.py shell < ../frontend/e2e/catalog-seed.py > /tmp/seed.json

It creates the org "Camille E2E" (currency MXN) with an admin and a member,
imports the Mercado Libre report in catalog/tests/mlInfo, and puts one variant
of the classic item MLM4549556180 on an invoice, so the page can show that
a sold product is not deleted; the account has a contact, for a new invoice.
Running it again starts the org over.
"""

import json
import sys
from pathlib import Path

from django.conf import settings

from accounts.models import Account
from contacts.models import Contact
from catalog.importers.mercadolibre import import_mercadolibre
from catalog.models import CatalogProduct, Listing
from common.models import Org, Profile, User
from common.serializer import OrgAwareRefreshToken
from common.tasks import clear_rls_context, set_rls_context
from invoices.models import Invoice, InvoiceLineItem

ORG = "Camille E2E"
REPORT = Path(settings.BASE_DIR) / "catalog" / "tests" / "mlInfo"

for old in Org.objects.filter(name=ORG):
    set_rls_context(old.id)
    # An invoice protects its account, and so its org, from deletion.
    Invoice.objects.filter(org=old).delete()
    old.delete()
org = Org.objects.create(name=ORG, default_currency="MXN")
set_rls_context(org.id)


def person(email, role):
    user, _ = User.objects.get_or_create(email=email)
    user.set_unusable_password()
    user.save()
    profile = Profile.objects.create(user=user, org=org, role=role, is_active=True)
    token = OrgAwareRefreshToken.for_user_and_org(user, org, profile)
    return {"access": str(token.access_token), "refresh": str(token)}


admin = person("admin@camille.test", "ADMIN")
member = person("member@camille.test", "USER")
report = import_mercadolibre(
    org, REPORT / "characteristics.xlsx", REPORT / "prices.xlsx"
)

# The classic item; a user-product family has the same title.
sold = Listing.objects.get(channel__org=org, external_id="MLM4549556180").product
variant = sold.variants.order_by("size_sort").first()
account = Account.objects.create(org=org, name="Boutique Luna")
Contact.objects.create(
    org=org, first_name="Ana", last_name="Luna", email="ana@camille.test", account=account
)
invoice = Invoice.objects.create(
    org=org, invoice_title="Venta boutique", account=account, currency="MXN"
)
InvoiceLineItem.objects.create(
    org=org,
    invoice=invoice,
    product=variant,
    name=variant.name,
    quantity=1,
    unit_price=variant.price,
)
products = CatalogProduct.objects.filter(org=org).count()
clear_rls_context()

sys.stdout.write(
    json.dumps(
        {
            "org": str(org.id),
            "admin": admin,
            "member": member,
            "sold_product": str(sold.id),
            "sold_product_name": sold.name,
            "products": products,
            "rejected": len(report.rejected),
        }
    )
    + "\n"
)
