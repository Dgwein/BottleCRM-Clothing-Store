"""Import an online channel's sales report into an org's orders.

    python manage.py import_channel_orders --org "My Store" \\
        --channel MERCADOLIBRE "Ventas.xlsx"

Safe to run again on every new report: see `orders.channel_import`. Every
run is kept, with its log, in the admin's Import runs.
"""

from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from common.models import Org
from common.tasks import clear_rls_context, set_rls_context
from orders.channel_import import (
    CHANGE,
    LEVELS,
    MAX_REPORT_BYTES,
    record_import,
    summary,
)
from orders.channels import PARSERS


class Command(BaseCommand):
    help = "Import a sales channel report (.xlsx) into an org's orders."

    def add_arguments(self, parser):
        parser.add_argument("file", help="Path to the channel's report.")
        parser.add_argument("--org", required=True, help="Org name or id.")
        parser.add_argument(
            "--channel", required=True, choices=sorted(PARSERS), type=str.upper
        )

    def handle(self, *args, **options):
        org = self._resolve_org(options["org"])
        path = Path(options["file"])
        try:
            with path.open("rb") as handle:
                data = handle.read(MAX_REPORT_BYTES + 1)
        except OSError as exc:
            raise CommandError(f"Cannot read {path}: {exc.strerror}.") from None
        if len(data) > MAX_REPORT_BYTES:
            raise CommandError("The report exceeds the 10 MB limit.")

        set_rls_context(org.id)
        try:
            run, result = record_import(
                org, options["channel"].upper(), path.name, data, "COMMAND"
            )
        finally:
            clear_rls_context()
        if result is None:
            raise CommandError(f"{run.error} (import run {run.pk})")

        self.stdout.write(f"Import run {run.pk}: {run.get_outcome_display()}.")
        self.stdout.write(summary(result))
        for level, label in LEVELS.items():
            entries = result.of_level(level)
            if not entries:
                continue
            # What went wrong goes to stderr, so a cron job can mail just that.
            out = self.stdout if level == CHANGE else self.stderr
            out.write(f"\n{label} ({len(entries)}):")
            for entry in entries:
                out.write(f"  {entry.sale or '-'}: {entry.message}")

    def _resolve_org(self, value: str) -> Org:
        try:
            return Org.objects.get(id=value)
        except (Org.DoesNotExist, ValueError, ValidationError):
            pass
        matches = list(Org.objects.filter(name=value))
        if not matches:
            raise CommandError(f"No org found matching {value!r}.")
        if len(matches) > 1:
            ids = ", ".join(str(o.id) for o in matches)
            raise CommandError(f"Multiple orgs named {value!r}; pass an id: {ids}")
        return matches[0]
