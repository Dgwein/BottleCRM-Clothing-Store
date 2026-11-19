# Catalog: changes to the distribution

The product catalog ([Product catalog](catalog.md)) is mostly new code in its own app and its own
pages. This page lists everything it changes in files that come from the original distribution,
so that a merge with a newer upstream release can be done file by file. Version: the whole
feature is release 1.45.0 (from 1.44.0).

Everything here was checked with the tests in `backend/catalog/tests/tests.py` (cases A to L),
the frontend unit and route tests (W and R) and the browser tests (V); see "After merging" below.

## New files (no upstream counterpart)

| Path | What |
|---|---|
| `backend/catalog/` | The app: models, API, importer and the background import runs (`imports.py`, `tasks.py`), management command, tests, migrations `0001` to `0004` (`0002` and `0004` enable row-level security), and the test report in `tests/mlInfo/`. |
| `backend/common/org_deletion.py` | Refusing to delete an org with sales (invoices, orders, recurring invoices) or with deals nobody agreed to lose; `delete_org` for the admin path. |
| `backend/common/templates/admin/common/org/delete_confirmation.html` | The Django admin's delete page for an org: says it cannot be undone, names its sales and its deals, and asks for a tick for each. |
| `backend/common/migrations/0069_alter_securityauditlog_event_type.py` | Adds `ORG_DELETED` to the audit log's event choices. No data change. |
| `backend/invoices/migrations/0018_catalog_variants_and_sales_channel.py` | The variant columns on `Product` and `Invoice.sales_channel`. |
| `backend/orders/migrations/0005_order_sales_channel.py` | `Order.sales_channel`. |
| `frontend/src/lib/server/v2/catalog.ts`, `catalog.test.ts` | The catalog's web data layer and its tests. |
| `frontend/src/routes/(app)/invoices/catalog/` | The catalog pages (products, a product, new product, categories, attributes, sales channels, import and an import run) and their route tests. |
| `frontend/e2e/` | The browser tests, their seed and README. |
| `docs/architecture/catalog.md`, this page | Documentation. |

## Changed files: backend

| Path | Change | Why |
|---|---|---|
| `crm/settings.py` | `"catalog"` in `INSTALLED_APPS`, after `invoices`. Three `ENUM_NAME_OVERRIDES`: `ListingStatusEnum`, `SalesChannelKindEnum`, `ImportRunStatusEnum`. | Registers the app. The overrides keep the generated enum names stable (a second `kind` or `status` enum would otherwise get a hashed name). |
| `pyproject.toml` | `version` 1.45.0; `"catalog"` in the wheel `packages` and the sdist `include`. | House rule: a new app is packaged, and a feature bumps the minor version. |
| `uv.lock` | The `django-crm` version line, 1.45.0. | Mirrors `pyproject.toml`. |
| `common/app_urls/__init__.py` | `path("catalog/", include("catalog.urls", namespace="api_catalog"))`. | Mounts `/api/catalog/`. |
| `common/modules.py` | `"api_catalog": Requires(any_of=("invoices", "deals"))` in `NAMESPACE_MODULES`. | The catalog is on whenever the products it prices are. |
| `common/scopes.py` | `"catalog"` in `API_RESOURCES`. | Personal API tokens can be scoped to it. |
| `common/rls/__init__.py` | The twelve catalog tables in `ORG_SCOPED_TABLES`. | Row-level security, enabled by `catalog/0002_enable_rls` and `catalog/0004_enable_rls_import_runs`. |
| `common/apps.py` | `import common.org_deletion` in `ready()`. | Connects the org deletion guard. |
| `common/admin.py` | `OrgAdmin` registered: read-only, deletion for superusers only, one org at a time (no bulk delete); the page says it cannot be undone and asks for a tick for the sales and one for the deals. | An org with sales is deleted only by a superuser, and one with deals only once someone agreed, after being told what goes with it. |
| `common/audit_log.py` | Event type `ORG_DELETED` and `audit_log.org_deleted(...)`. | The deletion of an org is recorded, with its sales and the number of deals. |
| `common/module_deletes.py` | `catalog.SalesChannel` in `DELETE_GUARDS` (its invoices) and in `ENTRY_POINTS`. | While Invoices is switched off, deleting a channel its invoices record answers 409 naming the module, like deleting an account. `common/tests/test_module_deletes.py` requires every such link to be guarded. |
| `common/saved_views.py` | `sales_channel` in the invoice list's saved-view parameters. | The invoice list reads it, and a saved view may keep it. |
| `common/tests/test_audit_log_not_exposed.py` | `catalog/tests` in `ALLOWED`. | The catalog tests assert the `ORG_DELETED` row is written; they serve nothing. |
| `common/tests/test_saved_views.py` | `sales_channel` among the id-valued sample parameters. | Follows `common/saved_views.py`. |
| `invoices/models.py` | `Product`: `catalog_product`, `variant_key`, `size` (default `Unique`), `size_sort`, `stock`, `gtin`, four package measures, an index on `(org, size)`, a unique `(catalog_product, variant_key)` and a `save()` that writes the size in the store's spelling. `Invoice.sales_channel` (nullable, PROTECT). | A variant is a `Product`, so line items price variants with no change; a sale records its channel. |
| `invoices/serializer.py` | `ProductSerializer.catalog_product` (read-only). `ProductCreateSerializer.validate`: a variant is not renamed here. `InvoiceListSerializer` and `InvoiceSerializer`: `sales_channel` (`{id, name, kind}`). `InvoiceCreateSerializer`: `sales_channel_id` and its validation (the org's, in use unless already the invoice's). | See [Product catalog](catalog.md#sales-channels). |
| `invoices/api_views.py` | Invoice list filter `sales_channel=<id>`; `sales_channel` in the list's `select_related`; a duplicated invoice keeps its channel while it is in use; `DELETE /api/invoices/products/<id>/` refuses a catalog variant that something sells (409). | Same rules through the old products screen as through the catalog. |
| `invoices/admin.py` | `sales_channel` in `InvoiceAdmin` (raw id, CRM Integration fieldset). | |
| `orders/models.py`, `orders/admin.py` | `Order.sales_channel` (nullable, PROTECT), shown in the admin. | Orders have no API or screen; the field is there for when they do. |

Behaviour the original distribution changes:

- **Deleting an organization.** Before: no screen or API deleted an org, and deleting one from a
  shell failed with a `ProtectedError` whenever an invoice had an account, while an org with only
  orders was deleted with them. Now: an org with invoices, orders or recurring invoices is refused
  on every path (`OrgHasSales`); through `delete_org` (the Django admin), one with deals is refused
  until the person deleting agrees they go too (`OrgHasDeals`). A superuser deletes it from the
  Django admin after confirming each. Code that deletes an org with deals but no sales directly
  still can, as before (the original tests do).
- **Deleting a product** through `/api/invoices/products/`: a catalog variant that something
  sells answers 409. A standalone product deletes as before.
- **Renaming a product** through `/api/invoices/products/`: a catalog variant's name and category
  are refused (they are built from its catalog product). A standalone product is unchanged.

## Changed files: frontend

| Path | Change |
|---|---|
| `src/lib/api-schema.d.ts` | Regenerated (`pnpm api:types`). Never merge it by hand: regenerate it after the backend merge. |
| `Dockerfile`, and `.env.docker` at the repository root | `BODY_SIZE_LIMIT` 6M to 45M: a catalog import sends two files of up to 20 MB in one request. Every other route's largest body grows with it. |
| `src/lib/components/import/ImportFlow.svelte`, `src/lib/components/tickets/TicketImportDrawer.svelte` | Comments only: the image's limit is now 45M. |
| `src/lib/v2/tabs.ts`, `tabs.test.ts` | A Catalog tab in the Invoices set; the catalog's own tab set (Products, Categories, Attributes, Sales channels, Import for admins). |
| `src/lib/v2/modules.ts`, `modules.test.ts` | `/invoices/catalog` is on with Invoices or Deals. |
| `src/lib/server/v2/products.ts`, `routes/(app)/invoices/products/+page.svelte` | A product row says when it is a catalog variant, linking to its product. |
| `src/lib/server/v2/invoices.ts` | The invoice detail carries `sales_channel`. |
| `routes/(app)/invoices/new/+page.server.ts`, `+page.svelte` | "Sales channel" picker (optional; channels in use). The form still loads if the channels cannot be read. |
| `routes/(app)/invoices/[id]/+page.svelte` | "sold through <channel>" under the status path, and "Sold through" in the side rail. |

## Changed files: docs and the phone

| Path | Change |
|---|---|
| `mkdocs.yml` | Two pages under Architecture. |
| `docs/architecture/data-model.md` | The Catalog entry. |
| `docs/reference/management-commands.md` | `import_mercadolibre`. |
| `docs/api/endpoint-index.md` | Regenerated (`scripts/generate_endpoint_index.py`); regenerate after merging, do not hand-merge. |
| `docs/self-hosting/docker.md`, `docs/reference/environment-variables.md`, `docs/integrations/csv-import.md` | `BODY_SIZE_LIMIT` is 45M in the published image, and why. |
| `mobile/docs/PARITY.md` | Backlog rows B36 (the catalog) and B37 (an invoice's sales channel): the phone does not have them yet. |

## Migrations and their order

```
common 0068 ─┬─> common 0069 (audit log choices)
             └─> catalog 0001 <── invoices 0017
                    ├─> catalog 0002 (RLS)
                    ├─> catalog 0003 (import runs) ─> catalog 0004 (their RLS)
                    ├─> invoices 0018 (variant columns, Invoice.sales_channel)
                    └─> orders 0005 (Order.sales_channel)   <── orders 0004
```

If upstream has added its own `common/0069`, `invoices/0018` or `orders/0005`, Django will see two
leaf nodes in that app. If this feature's migrations are already applied to a database, keep their
names and run `uv run python manage.py makemigrations --merge` for each app that reports it. If
they were never applied anywhere, renumbering them after upstream's (and updating the
`dependencies` that name them) is equally fine.

To take the feature out of a database: `migrate orders 0004`, `migrate invoices 0017`,
`migrate common 0068`, then `migrate catalog zero`. This drops every catalog row, the variant
columns and the sales channels on invoices and orders.

## Merging, step by step

1. Merge `backend/` and resolve the files in the tables above; keep both sides of every list
   (`INSTALLED_APPS`, `packages`, `ORG_SCOPED_TABLES`, `NAMESPACE_MODULES`, `API_RESOURCES`,
   `ENUM_NAME_OVERRIDES`, the audit log's `EVENT_TYPES`).
2. Set `version` to upstream's next minor release in `pyproject.toml`, then `uv lock`.
3. `uv run python manage.py makemigrations --check --dry-run` (merge migrations if it asks, see
   above), then `migrate`.
4. `cd frontend && pnpm api:types`, then merge the frontend files in the table above.
5. `cd backend && uv run python scripts/generate_endpoint_index.py`.

The catalog import runs in a Celery worker: a deployment that runs the app needs its worker
running (docker-compose already has one), or import runs stay queued.

## After merging

```
cd backend
uv run pytest catalog/tests/tests.py --no-cov            # A to L
uv run pytest --no-cov -m "not postgres_only"            # the whole suite
DJANGO_SETTINGS_MODULE=crm.test_settings_postgres DBUSER=<non-superuser role> \
  uv run pytest catalog/tests/tests.py --no-cov -m postgres_only   # J2, J3: RLS
cd ../frontend
pnpm run check && pnpm test && pnpm run lint
```

Then the browser tests, as `frontend/e2e/README.md` describes.
