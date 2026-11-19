# Online channel orders

A store that also sells online (Mercado Libre, Amazon, more later) imports each channel's sales
report into orders. The import is one-way: nothing is sent back to the channel. Import the report
every time you download a fresh one. Sales already imported are updated in place, never duplicated.

The code is `backend/orders/`: `channels/` holds one parser per channel, `channel_import.py` the
sync, and `inventory.py` the stock and work order rules.

## Importing a report

Either way runs the same import and reports the same log.

- **In the Django admin:** Orders > **Import channel report** (top right of the orders list).
  Choose the channel and the file (and, working across the whole instance, the organization).
  The page then opens the run, with its counts and its log. Needs permission to add orders.
- **From the command line:**

  ```bash
  cd backend
  uv run python manage.py import_channel_orders --org "My Store" --channel MERCADOLIBRE Ventas.xlsx
  ```

  `--org` takes an org name or id. It prints the run's id, its counts and its log: changes to
  stdout, everything else to stderr, so a scheduled job can mail just the problems.

The file can be up to 10 MB.

| Channel | Report |
| --- | --- |
| `MERCADOLIBRE` | Seller Central, Ventas: the `.xlsx` sales report |
| `AMAZON` | Not yet: the channel exists on orders, but there is no report parser |

A new channel is one parser in `backend/orders/channels/`, registered in `PARSERS`. It returns the
channel-neutral `ChannelOrder`/`ChannelLine` from `channels/base.py`, and everything below applies
to it unchanged.

### The import log

Each sale is imported on its own. One that cannot be imported is rolled back whole and the rest go
on. A file that cannot be read at all (not a sales report, not an `.xlsx`, too large) imports
nothing and says why. Every run reports:

| Level | Meaning | Examples |
| --- | --- | --- |
| Not imported | The sale was not saved, and why | An empty unit price, a quantity of 0 or below, an item listed twice, a pack with no products, a failure while saving |
| Action required | Imported, but someone has to follow up | A product the import created, an item with no listing or SKU, a return to inspect |
| Warning | Imported, with something read in part or skipped | An unreadable sale date, a pack with fewer products than it announces, an unknown status, a row with no sale number, a sale twice in one report |
| Change | What the import did | Order created, status changed, stock taken or put back, work orders opened |

A re-import that finds nothing new logs no changes. When a run leaves sales out, the admin says so
as soon as it ends ("N sale(s) were not imported", linking to them and their reasons), and the
command lists them on stderr.

Every run, from the admin or the command line, is kept in **Orders > Import runs**: who imported
which file (name, size, SHA-256) when, its outcome (imported; imported, with issues; nothing
imported, and why), its counts, and links to its log by level. **Import log entries** lists the
entries of every run, filtered by level, channel and outcome, and searched by sale number: that
is where to find every sale that was not imported, and why. Runs are read-only.

## What an import does

- **One order per sale.** An order is matched on its org, channel and channel sale id
  (`external_id`, unique per org and channel). A Mercado Libre sale of several products ("Paquete
  de N productos") is one order with one item per product, and each item has its own channel id
  and status.
- **Sales missing from a report are left alone**, since a report can cover only a date range.
- **Channel sales are booked to one account** named after the channel ("Mercado Libre"), created
  on first import. The buyer's name and shipping address are on the order.
- **Items link to catalog products through listings.** A `ChannelListing` is one channel listing
  and variant. The item gets the listing's product; a new listing is matched to the product with
  the same SKU, else a product is created for it (see below). To merge a created product into one
  you already had, point the listing at it in the Django admin; the next import moves open items
  across, with their stock.

### Products the import creates

The report has no catalog data, so a new product gets what the report has, and its listing is
flagged (`product_created`, a filter in Channel listings) and logged as an action required:

| Field | Value |
| --- | --- |
| `name` | The listing title, plus the variant in brackets; the listing id or SKU when there is no title |
| `description` | From the report: the listing title, then the variant; the listing id or SKU when there is no title. Never empty |
| `sku` | The report's SKU, when it has one; else empty |
| `price` | From the report: the unit price of the sale that created it, which may be a promotional price |
| `currency` | The report's currency (MXN for Mercado Libre Mexico) |
| `category` | Empty |
| `is_active` | Yes |
| `stock_quantity` | Empty, read as 0: every sale of it is prepared until stock is entered |
| `availability_days` | Empty, read as 0: due on the sale date until a time is entered |

Name, price and description are mandatory for a product, and none is ever left empty: all three
come from the report. **A sale whose unit price is empty (or unreadable) is not imported at all**:
neither its order nor any of its products is created, and the import log says so ("the unit price
is empty"). A pack with one such item is refused whole. A price of 0 is a price. A sale already
imported is left as it was.

How the product was made ("Created by the *channel* import from listing *id*, variant *variant*,
priced at the unit price of the sale that created it.") is an internal comment on the product, not
its description. Stock, availability time and category have to be completed by hand; the action
required says so.

### Statuses

The channel's status text is kept as it is (`channel_status`, with its detail) and normalised into
`fulfillment_status`:

| `fulfillment_status` | Mercado Libre examples | Order `status` |
| --- | --- | --- |
| `PENDING` | Esperando disponibilidad de stock, Etiqueta impresa | `ACTIVATED` |
| `SHIPPED` | En camino | `ACTIVATED` |
| `DELIVERED` | Entregado, Venta entregada, Mediación finalizada. Te dimos el dinero. | `COMPLETED` |
| `CANCELLED` | Cancelada por el comprador, Cancelaste la venta | `CANCELLED` |
| `RETURNED` | Devolución finalizada, Reclamo cerrado con reembolso al comprador | `CANCELLED` |

An order takes its most active item's status: one item still pending keeps the order pending. A
status the parser does not recognise is a warning; an existing item keeps its previous status and a
new one is treated as pending.

## Stock and work orders

Each catalog product carries `stock_quantity` (physical units on hand) and `availability_days`
(days to have it ready). Both are edited on the product in the Django admin. Empty means no
information, and reads as 0. Every channel draws on the same product stock.

An item still to be shipped is planned once, when it is first seen: it takes what stock it can, one
unit per unit sold, and records that as `stock_allocated`. Then its sale gets:

- **one `PACKAGING` work order per sale**, always, for all the units still to be shipped;
- **one `PREPARATION` work order per product** of the sale whose stock falls short, for the units
  short, whatever the quantity. Two items of the same product share it. An item with no catalog
  product gets its own.

| Stock of the product | Taken from stock | Work orders for that product |
| --- | --- | --- |
| 0, or empty | none | `PREPARATION` for the quantity |
| above 0, below the quantity | all of it | `PREPARATION` for the units short |
| the quantity or more | the quantity | none |

and the sale's `PACKAGING` covers every unit in all three cases. For example, a sale of product A
x1, B x1 and C x2, with no stock, gets three preparation orders (A 1, B 1, C 2) and one packaging
order for 4 units. With 1 of A and 9 of C in stock, a sale of A x5, B x4 and C x1 gets two (A 4,
B 4; C is covered) and one packaging order for 10 units.

**A packaging order cannot be marked done while any preparation order of its sale is open**: the
admin action refuses it and says how many are open. Preparations of other sales, and cancelled
ones, do not hold it.

### Due dates

The item's `due_date` is the sale date plus the product's availability time, counted as the
channel's preference says. The order's is the latest of its items that were not cancelled, or of
all of them when every item was; returned items count, since they were delivered. Work orders are due on
their items' date.

Set the preference in the Django admin, **Orders > Channel preferences**: calendar days or working
days, per channel. It is the same for every organization, since it is the channel's rule. Working
days are the weekdays the org's default business calendar is open, less its holidays; Monday to
Friday when it has no calendar. The defaults are calendar days for Mercado Libre and the store,
working days for Amazon.

Due dates are set when a sale is first imported, and later imports do not recalculate them: the
channel remains the order's master. A change of preference, or of a product's availability time,
applies to sales imported after it.

To apply such a change to orders already imported, use **Recalculate due dates** on the orders
list: choose the channel and a sale date (today or earlier). Every order of that channel sold on
or after the date gets its due dates set again from the current preference and availability
times: its items, the order, and its open preparation and packaging orders. Finished work orders
keep theirs. Other channels, earlier sales and other organizations are left alone. Needs
permission to change orders.

Delivered, cancelled and returned orders are recalculated too. Their due dates are not only
history: penalties for late delivery (a future module) are computed from them, so every order's
due date has to follow the rules in force.

### When a later report changes the sale

- **Cancelled**, or refunded, before it shipped: the item's units go back in stock, what was set
  aside by a finished preparation included. The sale's open work orders shrink to what is still to
  be shipped, or are cancelled when nothing is. Finished ones stay done.
- **Quantity or product changed** before it shipped: the item is released and planned again, and
  the open work orders resized.
- **Shipped or delivered**: the sale's open work orders are marked done.
- **Returned, or cancelled, after it shipped**: nothing is restocked automatically. A return
  inspection is opened instead (see below).

A sale first seen already shipped, delivered, cancelled or returned is history, whatever the
channel or report: it touches no stock, opens no work order and no return inspection. That is what
makes the first import of a long report safe. If such a sale later comes back, it is inspected like
any other.

Marking a preparation order done (the "Mark selected work orders as done" admin action) sets the
units it made aside for the sale's items of that product, so a cancellation after that puts them in
stock too. A finished packaging order makes no units, so it sets nothing aside.

## Returns

A return inspection is a work order of kind `INSPECTION`, one per returned item. An import opens it
when the report shows that an item it had already seen shipped or delivered is now returned or
cancelled, logs it as an action required, and goes on: the import does not wait for it. The
inspection itself can take hours or days, and is closed later by accepting or rejecting the goods.
It is listed with the other work orders (filter by kind) and cannot be marked done like them.

Goods that come back wait in **Orders > Return inspections** in the Django admin. Open one, check
the goods, set the **quantity to restock** (the units fit for sale, from 0 to the quantity
returned) and any notes (**Save notes** keeps it open), then:

- **Accept and restock** adds that quantity to the product's stock;
- **Reject (do not restock)** closes it without touching stock.

The list has the same two choices as bulk actions, and filters by status, outcome and channel. A
decided inspection is done, with its outcome (accepted or rejected), read-only, records who decided
it and when, and cannot be decided again. Inspections are opened only by the import; they cannot
be added or deleted by hand.

## Seeing and querying orders

In the Django admin, **Orders** lists every order with its channel, buyer, status, fulfillment
status, due date, item count and open work orders. It filters by channel, status, fulfillment
status, due date (overdue, today, the next 7 days, none), whether work orders are open, whether
returns wait for inspection, and sale date; and it searches the channel sale and item ids, buyer,
account, listing id, product name and SKU. **Order line items** and **Work orders** list and
filter the same way, and **Channel listings** is where a listing is pointed at a product.

On a channel order, the fields the import writes (status, dates, amounts, buyer, shipping, items)
are read-only, since the next report would overwrite an edit. Orders made in the CRM (`STORE`)
stay editable.

The admin works in one organization or across the whole instance, chosen on sign-in (see
[PostgreSQL and RLS](../self-hosting/postgresql-and-rls.md#the-django-admin)). In an
organization, every list, filter and form here shows only its records.

## Data

| Model | Table | Notes |
| --- | --- | --- |
| `Order` | `orders` | Adds `channel`, `external_id`, `channel_status`, `channel_status_detail`, `fulfillment_status`, `buyer_name`, `due_date`, `last_synced_at` |
| `OrderLineItem` | `order_line_item` | Adds `external_id`, `listing`, `channel_status`, `fulfillment_status`, `due_date`, `stock_allocated`, `fulfillment_planned` |
| `ChannelListing` | `order_channel_listing` | Unique per org, channel, listing id and variant; `product_created` when the import made its product |
| `ChannelPreference` | `order_channel_preference` | One per channel, for the whole instance: how it counts availability time |
| `WorkOrder` | `order_work_order` | Belongs to the sale; `kind` `PREPARATION` (with its `product`, or its `line_item` when the item has none), `PACKAGING` or `INSPECTION` (with its `line_item`, one per item, and `restock_quantity`, `outcome`, `notes`, `completed_by`, `completed_at`); `status` `PENDING`, `DONE` or `CANCELLED` |
| `ReturnInspection` | (none) | A proxy of `WorkOrder`, the inspections only, behind the Return inspections screen |
| `ImportRun` | `order_import_run` | One per import, with its file, outcome, error and counts |
| `ImportRunEntry` | `order_import_run_entry` | One per log line of a run: level, sale, detail |
| `Product` | `product` | Adds `stock_quantity`, `availability_days` |

The listing, work order, import run and import log tables are org-scoped, registered in
`ORG_SCOPED_TABLES` and stamped with RLS policies by `orders/0006`. The channel preference table
holds no tenant data and has no org. The command sets the RLS context for the org it imports into.
