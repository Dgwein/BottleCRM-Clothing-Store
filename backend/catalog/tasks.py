from celery import shared_task

from catalog.imports import execute
from catalog.models import ImportRun
from common.tasks import clear_rls_context, set_rls_context


@shared_task
def run_catalog_import(run_id, org_id):
    """Carry out a queued import run (see catalog/imports.py). A worker runs
    no middleware, so the org's RLS context is set here."""
    set_rls_context(org_id)
    try:
        run = ImportRun.objects.filter(
            pk=run_id, org_id=org_id, status=ImportRun.QUEUED
        ).first()
        if run is not None:
            execute(run)
    finally:
        clear_rls_context()
