# Product catalog

The catalog (`backend/catalog/`) describes what a store sells when one product comes in many
sizes, colors and designs, and is sold through more than one sales channel: online channels
(Mercado Libre, Amazon, its own web shop) and physical stores. It was built for a children's
clothing store whose master catalog lives on Mercado Libre, and it is loaded from Mercado Libre's
bulk-edit report, from the command line or from the web.

What this feature changes outside `backend/catalog/` (models, migrations and screens of the
original distribution) is listed file by file in
[Catalog: changes to the distribution](catalog-distro-changes.md), for merging.

## The model

```
Category ──< CategoryAttribute >── Attribute ──< AttributeOption
   │                                   │
   │                                   └──< ChannelAttribute >── SalesChannel ──< invoices.Invoice
   │                                                               │   │          (sales_channel)
CatalogProduct ──< invoices.Product (variant: size, stock, price)  │   └──< orders.Order
   │                    │                                          │        (sales_channel)
   ├──< AttributeValue >┘   (product-wide when variant is empty)   │
   │                                                               │
   └──< Listing >──────────────────────────────────────────────────┘  (online channels only)
           │
           └──< ListingVariant >── invoices.Product   (the channel's ids only)
```

- **`CatalogProduct`** is one product as the store thinks of it, independent of any channel.
- **A variant is an `invoices.Product`** linked back through `Product.catalog_product`. That makes
  every existing line item, product picker and price variant-aware with no change to invoices,
  estimates or deals. A `Product` with no `catalog_product` is the single-dimension product the
  CRM always had, and keeps working as before.
- **Size and stock are columns** on the variant (`Product.size`, `Product.stock`), not attributes,
  because lists filter, sort and bulk-edit by them. A variant with no size is `Unique`, one with
  no stock has 0. `Product.size_sort` orders sizes by age in SQL. A product carries only the sizes
  its variants have; nothing requires a product to come in every size.
- **`Attribute`** is one characteristic (material, sleeve type), defined once per org and shared
  by every category that uses it. Its `data_type` is text, number (with a unit), yes or no, or an
  option list; only option lists can hold several values (occasions, styles).
- **`AttributeValue`** stores one value. Nothing is stored for an attribute a product does not have.
  A row with an empty `variant` holds for every variant of the product; a variant's own row for the
  same attribute overrides it.
- **`CategoryAttribute`** says which attributes a category uses and in which role:
  - a **variant axis** (color, fabric design, design name) is one of the mandatory fields that,
    with the size, identify a variant. `Product.variant_key` is the size and those values in a
    fixed order, unique per catalog product, so two variants never share all of them.
  - a **characteristic** describes the product without splitting it into variants.

### Sales channels

A **`SalesChannel`** is the shared record for where the store sells. Catalog, orders, invoices and
fulfillment all point to this same entity. It is not an `Account`: an invoice is addressed to the
customer who pays it (its account), and records separately the channel the sale came through
(`Invoice.sales_channel`, `Order.sales_channel` and the fulfillment sale). A channel also holds
its late-delivery penalty. Two kinds:

- **Online** (`ONLINE`): a marketplace or web shop. It has a market id (`site`, such as `MLM`),
  publishes **`Listing`s** under its own ids, and may name attributes its own way
  (**`ChannelAttribute`**: Mercado Libre calls material `MAIN_MATERIAL` on dresses and `MATERIALS`
  on aprons, and both feed `material`).
- **Physical store** (`STORE`): a shop with an address and a phone. It has no market id and no
  listings.

The catalog applies to every channel: any variant can be sold through any channel, at its one
price. A **`Listing`** is one publication on an online channel with its sale conditions (status,
listing type, shipping, warranty, days to ship, and when the channel last updated it);
**`ListingVariant`** records that a variant is published through it, under the channel's ids. It
holds no price and no stock.

Stock is one number per variant, the same for every channel, physical stores included.

A channel switched off (`is_active`) is not offered for new sales; an invoice that already records
it keeps it. A channel with listings, invoices, orders, fulfillment sales or delivery options is
not deleted (409, naming them): switch it off instead; while Invoices is switched off, the 409
names the module instead, as for an account. A channel with listings or attribute codes stays
online.

### Prices

Every variant has one price, `Product.price`, and it is the price everywhere and through every
channel: the one invoice, estimate and deal line items take, and the one loaded from Mercado
Libre. No variant is without a price: the API refuses a variant without a price above 0, and the
importer rejects such a row. Prices are in the org's currency (`Org.default_currency`); the
catalog holds no second currency. The importer loads the price from column I of the prices
report (`PRICE`) and ignores every other price in it (sale price, volume prices).

### Deleting

- A **variant** with no sales is deleted, whatever its siblings sold. One that a deal, order,
  invoice, estimate or recurring invoice sells is kept (409, naming what sells it), through the
  catalog API or `/api/invoices/products/`; retire it instead (`is_active`).
- A **product** is deleted only while none of its variants has sales; the refusal names the
  variants that do, since the others can still be deleted one by one.
- A standalone product (no catalog product) deletes as it always did.
- An **organization** with sales (invoices, orders or recurring invoices) is not deleted, by any
  path. Only a superuser can delete it with its sales, from the Django admin. An organization
  with deals is deleted only once whoever deletes it has agreed that the deals go too (asked by
  `delete_org`, which the admin uses; code that deletes an org directly has nobody to ask). The admin's
  delete page says that deleting an organization cannot be undone, names the sales and the deals,
  and asks for a tick for each (`common/org_deletion.py`). Estimates go with the organization
  without a question. The deletion is recorded in the security audit log (`ORG_DELETED`, with
  the sales and the number of deals).
- Deleting a category, attribute or option that is still in use answers 409.

### Rules worth knowing

- Once a category has variants, its axes are fixed: adding or removing an axis would leave the
  variants' keys stale, so the API refuses it. A product with variants keeps its category.
- Two variants of one product never share a size and every axis value.
- A variant's name is built from its product's name and axis values, and rebuilt when either
  changes. `/api/invoices/products/` refuses to rename a variant for that reason.
- A characteristic is stored once per product or variant: the database refuses a second row,
  and an import replaces the values it loaded before.

## API

Everything is under `/api/catalog/`. Any member reads; every write (and the import) is for an org
admin, like the rest of the product catalog. The endpoint index lists every route.

Products and variants filter by size (`size=`, repeatable, in any spelling: `size=2 Años` is
`2A`), by stock (`in_stock=true` or `false`) and by attribute with a repeatable `attr=code:value`.
Values of one code are alternatives and different codes must all match, and an attribute filter
counts product-wide values as well as a variant's own. On products, every filter must hold for the
same variant:

```
GET /api/catalog/variants/?size=2A&size=3A&attr=gender:Niñas&in_stock=true
GET /api/catalog/products/?attr=material:Algodón&category=<id>
GET /api/catalog/sizes/            the sizes in use, youngest first
GET /api/catalog/channels/         online channels and physical stores
GET /api/invoices/?sales_channel=<id>
```

Option values match without regard to case or accents, and through their aliases.

Attributes are written as an object keyed by code. An empty value removes the attribute:

```json
{
  "price": "499.00",
  "size": "0-3 M",
  "stock": 12,
  "attributes": {
    "color": "Rojo",
    "occasions": ["Fiesta", "Diario"],
    "width": { "value": 35, "unit": "cm" },
    "brand": null
  }
}
```

An invoice records its channel with `sales_channel_id` on create or update (one of the org's
channels, in use unless the invoice already has it), and reads it back as
`sales_channel: {id, name, kind}`. A duplicated invoice keeps its channel while the channel is in
use.

## Web pages

Invoices › Catalog has five tabs. Any member sees the first four; only an org admin sees the
Import tab and the buttons and forms that change anything.

- **Products**: 50 products to a page, filtered by name, SKU or listing id, category, size and
  stock, each with its sizes, variant count, stock, price range and listing count. New product
  creates one in a category.
- **A product**: its characteristics (edited in the fields its category lists), its variants
  (price, stock, size and whether it sells, edited on the row, and deleted on the row while they
  have no sales; Add a variant picks the category's axes), its listings, and Delete product. A
  refusal from the API (a duplicate variant, a price of 0, a product that is sold) is shown beside
  the form that caused it.
- **Categories**: add, rename and delete categories, and link attributes as characteristics or
  variant axes.
- **Attributes**: add attributes and their options, and see the channel code each one maps to.
- **Sales channels**: add an online channel (with its market id) or a physical store (with its
  address and phone), switch it off, delete it while nothing is listed on it or sold through it.
- **Import** (admins): one card per channel. Mercado Libre takes its two files; every other
  channel (Amazon, a physical store) is listed with its upload disabled until its report has been
  analyzed and an importer written for it. Preview runs the whole import and saves nothing;
  Import saves it. Either opens the run's page, which follows the run while it is queued or
  running and then shows the report: what was created and updated, the categories left with no
  products, every rejected row and every warning. A finished preview offers "Import these files".
  "Email me the report when it finishes" sends the report by email; a run that takes longer than
  a minute is emailed anyway. The page lists the recent runs.

A new invoice offers the sales channels in use ("Sales channel", optional); the invoice page shows
the one it was sold through.

The browser tests in `frontend/e2e/` drive these pages; their README says how to run them.

## Importing a channel's report

Each channel exports its catalog in a layout of its own, so each needs an importer written for
it. `catalog/imports.py` lists the ones that exist in `SOURCES`: Mercado Libre today. The import
page and `GET /api/catalog/import/sources/` offer every other channel of the org too, not
available, with the reason; files sent for it are refused. Adding a channel means analyzing its
files, writing its importer, and adding it to `SOURCES`.

From the web (Invoices › Catalog › Import), or the API it calls:

```
GET  /api/catalog/import/sources/            what can be imported, and what cannot yet
POST /api/catalog/import/runs/               multipart: source, dry_run, notify, and the files
                                             (characteristics, prices); answers 202 at once
GET  /api/catalog/import/runs/               the runs, latest first
GET  /api/catalog/import/runs/<id>/          one run: status, error, report
POST /api/catalog/import/runs/<id>/import/   import a finished preview's files
```

Everything here is for org admins, reads included (a report quotes the rows it read).

- **Limits.** Each file may be up to 20 MB, and unpack to at most 600 MB; a sheet may have up to
  200,000 rows. The web server must accept a request of both files: `BODY_SIZE_LIMIT=45M` in the
  published frontend image, and the same allowance in any proxy in front of it. The request
  checks the files (present, `.xlsx`, size, a readable zip) and stores them; anything else wrong
  with them fails the run, with the reason.
- **Runs.** A run (`ImportRun`, with its files in `ImportFile`) is carried out by a Celery task
  (`catalog.tasks.run_catalog_import`), so no request waits on it; without a Celery worker runs
  stay queued. One run at a time per org: another is refused (409) while one is queued or
  running, unless that one was started over two hours ago (its worker is taken for lost). An
  import's files are deleted when it finishes; a preview's are kept for a day, for "Import these
  files", then dropped.
- **Email.** The report goes by email to whoever started the run when they asked for it, and
  whenever the run took longer than a minute (`EMAIL_AFTER`), whatever its outcome: what was
  created and updated, the categories with no products, how many rows were rejected and the
  warnings, and a link to the run's page. A mail server that refuses does not fail the run.
- **Speed and memory.** The workbooks are read in openpyxl's streaming mode and only their
  values are kept. See the measurements in `test-reports/round-5/large-report-measurement.log`.

From the command line, synchronously and with no email:

```
cd backend
uv run python manage.py import_mercadolibre --org "<org name or id>" \
    --characteristics catalog/tests/mlInfo/characteristics.xlsx \
    --prices catalog/tests/mlInfo/prices.xlsx [--dry-run]
```

Both files are required: a product needs its characteristics and its price and sale conditions.
The report lists what was created and updated, every category left with no products (with how
many of its rows were rejected), every row it rejected and why, and a warning for every row it
took but not as it stood. It is safe to re-run: listings and variants are matched by their
Mercado Libre ids and updated in place. Re-running it after correcting the org's default currency
moves every imported price to that currency and changes nothing else.

### What the report contains

- **Characteristics** has one sheet per category. Row 1 holds Mercado Libre's attribute ids, row 2
  each column's kind (`FIXED`, `VARIATION`, `ATTRIBUTE`, `ATTRIBUTE_UNIT`), row 4 the labels (yellow
  for mandatory fields, blue for characteristics), and one row per variant from row 5.
  `VARIATION` columns become variant axes and `ATTRIBUTE` columns characteristics.
- **Prices** has one sheet. A classic item has a header row and a row per variation; a "user
  product" item, where every variant is its own publication, is one row grouped with its siblings
  by family id.
- Both repeat values with formulas (`=M6`, `=K11&"     "`) whose cached results are not reliable,
  so the importer resolves the references itself.

### How rows become the catalog

- Rows group into one `CatalogProduct` by family id, or by item id for a classic item.
- A characteristic every variant agrees on is stored once on the product; otherwise on the
  variants that have it. A column nobody filled in anywhere creates nothing.
- A row with no price, a price of 0, or no row in the prices file at all is rejected: nothing of it
  is loaded, and the report names its sheet, row, publication and product number. So is a prices
  row with no characteristics row. A product none of whose rows is loaded is not created. Its
  category is, with every sheet of the report, and the report names it among the categories with
  no products (in the real report, "Sudaderas y hoodies": all 15 of its rows are rejected).
- Sizes go to `Product.size` in the store's spelling (`0-3 M`, `00-03 Meses` and
  `0 Meses - 3 Meses` are all `0-3 meses`; `2 Años` is `2A`), with the store's decisions applied
  (`catalog.text`): `0 meses` is RN; a bare number from 1 to 16 is years (`2` is `2A`); a bare
  range is months (`6-9` is `6-9 meses`); whole years written in months are years (`24 meses` is
  `2A`), except that `1A` is written `12 meses`. Heights such as `100` stay as written. A row with
  no size is `Unique`. One exception, by publication: the "Mi Primer Año Nuevo" baby sets
  (`MONTH_SIZED_PUBLICATIONS` in the importer) are sized 3, 6, 9 and 12 months.
- `QUANTITY` is the variant's stock (0 when empty). The price is the variant's price, in the org's
  currency.
- Two publications with the same size and axis values are one variant published twice. It is
  loaded from the active one. When both or neither are active, it is loaded from the one the
  channel updated last, if the report gives that date for every one of them (a `LAST_UPDATED`
  column, in either file: Mercado Libre's API calls it `last_updated`; the bulk-edit export does
  not have it, so today the file order decides); otherwise from the one later in the file. The other is ignored with a warning
  saying which rule decided, and nothing of it is kept. The date, when given, is kept on the
  listing (`Listing.channel_updated_at`).
- Some categories in the report leave out an axis that Mercado Libre's own variant label shows
  (`Rojo 12-18 M Plim Plim`). The part of the label no axis accounts for is recovered as a size
  when it reads as one, and as the design name otherwise, so those variants stay apart.
- A barcode is optional: a real code in the GTIN column goes to `Product.gtin`; anything else (ML's
  prompt, or a reason for having none) stores nothing.
- The importer's channel is the org's channel coded `mercadolibre`, created online on the first
  import. If that code belongs to a physical store, the import stops and saves nothing.
