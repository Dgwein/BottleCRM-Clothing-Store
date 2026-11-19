"""Load a Mercado Libre bulk-edit report (characteristics + prices) into one
org's catalog. Safe to re-run: rows are matched by their ML ids."""

import json

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from catalog.importers.mercadolibre import import_mercadolibre
from common.models import Org
from common.spreadsheet import SpreadsheetError
from common.tasks import clear_rls_context, set_rls_context


class Command(BaseCommand):
    help = "Import a Mercado Libre catalog report into an organization's catalog."

    def add_arguments(self, parser):
        parser.add_argument("--org", required=True, help="Org id or exact name.")
        parser.add_argument("--characteristics", required=True)
        parser.add_argument("--prices", required=True)
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change, then roll everything back.",
        )

    def handle(self, *args, **options):
        org = _find_org(options["org"])
        set_rls_context(org.id)
        try:
            report = import_mercadolibre(
                org,
                options["characteristics"],
                options["prices"],
                dry_run=options["dry_run"],
            )
        except (SpreadsheetError, OSError) as exc:
            raise CommandError(str(exc)) from None
        finally:
            clear_rls_context()
        self.stdout.write(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
        rejected = len(report.rejected)
        if rejected:
            rows = "1 row was" if rejected == 1 else f"{rejected} rows were"
            self.stdout.write(
                f'{rows} rejected and not loaded; each is listed under "rejected".'
            )
        for empty in report.empty_categories:
            why = (
                f"all {empty['rows']} of its rows were rejected"
                if empty["rows"]
                else "its sheet has no rows"
            )
            self.stdout.write(f"Category {empty['category']}: no products, {why}.")
        if options["dry_run"]:
            self.stdout.write("Dry run: nothing was saved.")


def _find_org(value):
    org = Org.objects.filter(name=value).first()
    if org is None:
        try:
            org = Org.objects.filter(id=value).first()
        except (ValueError, ValidationError):
            org = None
    if org is None:
        raise CommandError(f"No organization matches {value!r}.")
    return org
