"""Importing a channel's report into the catalog, in the background.

Each channel exports its catalog in a layout of its own, so each needs an
importer written for it. `SOURCES` holds the ones that exist (Mercado Libre
today). Every other channel of the org is offered too, but not available:
its upload stays disabled until its files have been analyzed and an importer
added here.

A run is started by a request, which checks the files and stores them, and is
carried out by a Celery task (`catalog.tasks.run_catalog_import`), so a large
report holds no request open. The report is emailed to whoever started the
run when they asked for it, and whenever the run took longer than
`EMAIL_AFTER`, since nobody waits that long on a page.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone

from catalog.importers.mercadolibre import check_upload, import_mercadolibre
from catalog.models import ImportFile, ImportRun, SalesChannel
from common.links import frontend_url
from common.spreadsheet import SpreadsheetError

logger = logging.getLogger(__name__)

EMAIL_AFTER = timedelta(seconds=60)
# A preview's files are kept this long, to be imported without re-sending.
KEEP_PREVIEW_FILES = timedelta(days=1)
# A run still queued or running after this is taken for lost (its worker
# died) and no longer stops a new one.
LOST_AFTER = timedelta(hours=2)


@dataclass(frozen=True)
class Source:
    code: str
    name: str
    files: tuple  # ((form field, label), ...)
    run: object  # (org, {field: file}, dry_run) -> ImportReport


SOURCES = {
    "mercadolibre": Source(
        code="mercadolibre",
        name="Mercado Libre",
        files=(
            ("characteristics", "Characteristics (one sheet per category)"),
            ("prices", "Prices and sale conditions (Publicaciones)"),
        ),
        run=lambda org, files, dry_run: import_mercadolibre(
            org, files["characteristics"], files["prices"], dry_run=dry_run
        ),
    ),
}


class ImportRefused(Exception):
    """Raised with a sentence safe to show. `status` is the HTTP status."""

    def __init__(self, message, status=400, field=None):
        super().__init__(message)
        self.status = status
        self.field = field


def not_available(name):
    return (
        f"Importing from {name} is not available yet: its report has to be "
        "analyzed and an importer written for its layout first."
    )


def sources(org):
    """Every source an admin is shown: the importers that exist, then each
    other channel of the org, not available."""
    channels = {c.code: c for c in SalesChannel.objects.filter(org=org)}
    listed = []
    for source in SOURCES.values():
        listed.append(
            {
                "code": source.code,
                "name": source.name,
                "available": True,
                "reason": "",
                "files": [{"field": f, "label": label} for f, label in source.files],
                "channel": channels.pop(source.code, None),
            }
        )
    for channel in channels.values():
        listed.append(
            {
                "code": channel.code,
                "name": channel.name,
                "available": False,
                "reason": not_available(channel.name),
                "files": [],
                "channel": channel,
            }
        )
    return listed


def _in_progress(org):
    return ImportRun.objects.filter(
        org=org,
        status__in=(ImportRun.QUEUED, ImportRun.RUNNING),
        created_at__gte=timezone.now() - LOST_AFTER,
    ).exists()


def _forget_old_previews(org):
    ImportFile.objects.filter(
        org=org,
        run__dry_run=True,
        run__created_at__lt=timezone.now() - KEEP_PREVIEW_FILES,
    ).delete()


def _queue(run):
    from catalog.tasks import run_catalog_import

    transaction.on_commit(
        lambda: run_catalog_import.delay(str(run.id), str(run.org_id))
    )


def start_import(org, user, code, uploads, *, dry_run, notify):
    """Check and store the files of a new run and queue it. `uploads` maps a
    form field to its uploaded file. Nothing is stored when anything is
    refused."""
    source = SOURCES.get(code)
    if source is None:
        channel = SalesChannel.objects.filter(org=org, code=code).first()
        raise ImportRefused(not_available(channel.name if channel else code))
    stored = {}
    for field, _label in source.files:
        try:
            stored[field] = (
                uploads[field].name,
                check_upload(uploads.get(field), field),
            )
        except KeyError:
            raise ImportRefused(f"The {field} file is required.", field=field) from None
        except SpreadsheetError as exc:
            raise ImportRefused(str(exc), field=field) from None
    if _in_progress(org):
        raise ImportRefused(
            "Another import is still running. Wait for it to finish.", status=409
        )
    _forget_old_previews(org)
    with transaction.atomic():
        run = ImportRun.objects.create(
            org=org,
            source=code,
            dry_run=dry_run,
            notify=notify,
            created_by=user,
            file_names={field: name for field, (name, _) in stored.items()},
        )
        for field, (name, data) in stored.items():
            ImportFile.objects.create(
                org=org, run=run, field=field, name=name[:255], content=data
            )
        _queue(run)
    return run


def import_previewed(preview, user, *, notify):
    """Queue the import of the files a finished preview checked."""
    if not (preview.dry_run and preview.status == ImportRun.DONE):
        raise ImportRefused("Only a finished preview can be imported.", status=409)
    files = list(preview.files.all())
    if not files:
        raise ImportRefused(
            "This preview's files are no longer kept. Send them again.", status=409
        )
    if _in_progress(preview.org):
        raise ImportRefused(
            "Another import is still running. Wait for it to finish.", status=409
        )
    with transaction.atomic():
        run = ImportRun.objects.create(
            org=preview.org,
            source=preview.source,
            dry_run=False,
            notify=notify,
            created_by=user,
            file_names=preview.file_names,
            preview_of=preview,
        )
        for f in files:
            ImportFile.objects.create(
                org=run.org, run=run, field=f.field, name=f.name, content=f.content
            )
        # The files now belong to the import; the preview does not keep a copy.
        preview.files.all().delete()
        _queue(run)
    return run


class _Upload:
    """A stored file as the importers read an upload."""

    def __init__(self, stored):
        self.name = stored.name
        self._data = bytes(stored.content)

    def read(self, size=-1):
        return self._data if size < 0 else self._data[:size]


def execute(run):
    """Carry out a queued run: import, keep the report, drop files no longer
    needed, and email the report when it is due. Runs under the org's RLS
    context (the task sets it)."""
    run.status, run.started_at = ImportRun.RUNNING, timezone.now()
    run.save(update_fields=["status", "started_at", "updated_at"])
    files = {f.field: _Upload(f) for f in run.files.all()}
    try:
        report = SOURCES[run.source].run(run.org, files, run.dry_run)
    except SpreadsheetError as exc:
        run.status, run.error = ImportRun.FAILED, str(exc)
    except Exception:
        run.status = ImportRun.FAILED
        run.error = "The import stopped on an unexpected error. Nothing was saved."
        raise
    else:
        run.status, run.report = ImportRun.DONE, report.as_dict()
    finally:
        run.finished_at = timezone.now()
        run.save(
            update_fields=["status", "report", "error", "finished_at", "updated_at"]
        )
        if not (run.dry_run and run.status == ImportRun.DONE):
            run.files.all().delete()
        if run.notify or run.finished_at - run.started_at > EMAIL_AFTER:
            email_report(run)
    return run


_COUNTS = (
    ("products", "products"),
    ("variants", "variants"),
    ("listings", "listings"),
    ("categories", "categories"),
    ("attributes", "attributes"),
    ("channels", "sales channels"),
)


def _duration(run):
    seconds = int((run.finished_at - run.started_at).total_seconds())
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes} min {seconds} s" if minutes else f"{seconds} s"


def email_body(run):
    source = SOURCES[run.source].name if run.source in SOURCES else run.source
    what = "preview" if run.dry_run else "import"
    lines = [f"The {source} {what} you started finished in {_duration(run)}."]
    if run.status == ImportRun.FAILED:
        lines += ["", f"It failed: {run.error}", "Nothing was saved."]
    else:
        report = run.report or {}
        if run.dry_run:
            lines += ["", "This was a preview: nothing was saved."]
        for key, heading in (("created", "New"), ("updated", "Updated")):
            counts = report.get(key, {})
            parts = [f"{counts[k]} {label}" for k, label in _COUNTS if counts.get(k)]
            if parts:
                lines.append(f"{heading}: {', '.join(parts)}.")
        empty = report.get("empty_categories", [])
        if empty:
            lines += ["", "Categories with no products:"]
            lines += [
                f"  {c['category']}: all {c['rows']} of its rows were rejected."
                for c in empty
            ]
        lines += [
            "",
            f"Rejected rows: {len(report.get('rejected', []))}",
            f"Warnings: {len(report.get('warnings', []))}",
        ]
    lines += [
        "",
        f"The whole report: {frontend_url(f'/invoices/catalog/import/{run.id}')}",
    ]
    return "\n".join(lines)


def email_report(run):
    user = run.created_by
    if not (user and user.is_active and user.email):
        return
    source = SOURCES[run.source].name if run.source in SOURCES else run.source
    what = "Catalog preview" if run.dry_run else "Catalog import"
    outcome = "failed" if run.status == ImportRun.FAILED else "finished"
    try:
        EmailMessage(
            f"{what} {outcome}: {source}", email_body(run), to=[user.email]
        ).send()
    except Exception as exc:  # the mail backends raise types of their own
        # The run is done either way; its page still has the report.
        logger.warning("Import report email not sent: %s", type(exc).__name__)
        return
    run.emailed_at = timezone.now()
    run.save(update_fields=["emailed_at", "updated_at"])
