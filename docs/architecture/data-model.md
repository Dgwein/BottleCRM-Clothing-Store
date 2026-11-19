# Data model

This page describes the shape every model in this codebase follows, the handful of ways core CRM
entities connect to each other, and a few conventions that are easy to assume rather than verify,
which is exactly why they're written down here with the file and line that makes each one true.

## Core entities

- **Lead** (`backend/leads/models.py`), a prospect not yet qualified into a customer.
  `LeadPipeline`/`LeadStage` give each org its own configurable Kanban columns.
- **Account** (`backend/accounts/models.py`), a company or customer. `AccountEmail`/
  `AccountEmailLog` support account-linked inbound mailboxes.
- **Contact** (`backend/contacts/models.py`), a person, optionally linked to an `Account`.
- **Opportunity** (`backend/opportunity/models.py`). A sales deal in progress.
  `OpportunityLineItem` holds quoted products; `DealPipeline`/`DealStage` are the org's configurable
  deal stages, each stage carrying its kind (open, won, lost) and its "how many days is too long in
  this stage" rotting thresholds; `SalesGoal` is a quota/target tracked against `Opportunity` data.
- **Case** (`backend/cases/models.py`), a support ticket. By far the largest cluster of supporting
  models: `CaseWatcher`, `CsatSurvey`, `Solution` (knowledge base), `CasePipeline`/`CaseStage`,
  `ReopenPolicy`, `EscalationPolicy`, `InboundMailbox`/`EmailMessage` (email-to-ticket),
  `RoutingRule`/`RoutingRuleState`, and `TimeEntry`.
  `Case.account` is a direct foreign key (not nullable through an intermediate table); Cases carry
  their own SLA fields (`sla_first_response_hours`, `sla_resolution_hours`,
  `first_response_at`, `resolved_at`) directly on the model.
- **Task** (`backend/tasks/models.py`). A to-do item, with its own configurable
  `TaskPipeline`/`TaskStage`, and a separate `Board`/`BoardColumn`/`BoardTask`/`BoardMember` family
  for freeform Kanban boards distinct from the Task pipeline.
- **Invoice** (`backend/invoices/models.py`): billing, alongside `Estimate`, `RecurringInvoice`,
  `Product`, `Payment`, and `InvoiceTemplate` in the same app. This app also owns the anonymous
  client-portal views (see [Multi-tenancy and RLS](multi-tenancy-and-rls.md#portal-tokens)).
- **Catalog** (`backend/catalog/models.py`): products with variants, shared attributes, sales
  channels (online channels and physical stores) and the listings online channels publish. A
  variant is an `invoices.Product` linked to a `CatalogProduct`, so line items price variants with
  no change. `Invoice.sales_channel` and `Order.sales_channel` record the channel a sale came
  through; the account stays the customer. See [Product catalog](catalog.md).
- **Order** (`backend/orders/models.py`), `Order`/`OrderLineItem`.
- **Order** (`backend/orders/models.py`), `Order`/`OrderLineItem`, plus `ChannelListing`,
  `WorkOrder` (preparation, packaging and return inspection), `ChannelPreference`, `ImportRun` and
  `ImportRunEntry` for sales imported from online channels (see
  [Online channel orders](../features/channel-orders.md)).
- **Sale** (`backend/fulfillment/models.py`): the fulfilment side of one order, its shared catalog
  channel and committed delivery time, with its `PreparationOrder`s and one `PackagingOrder`. A packed sale
  rides a `DispatchOrder` (one run of a `DeliveryOption`) through a `DispatchOrderItem`.
  `QueueAssignment` gives teams their order types.
  See [Fulfillment](#fulfillment).
- **Interaction** (`backend/interactions/models.py`): a call, meeting, email, WhatsApp message or
  SMS logged on a record, or an enquiry a lead made. It links to any of a lead, contact, account,
  deal and ticket, and `home` names the one it was logged on, whose visibility alone decides who
  sees it. Deleting the home record deletes the interaction; deleting any other linked record only
  unlinks it. A merge moves it to the surviving record. The lead's `first_response_at`,
  `first_response_by` and `last_outbound_at` are derived from its outbound interactions, and
  `Org.lead_response_since` marks when the org started measuring responses.

## Relationships

The lead-to-customer path is a one-time conversion, not a standing foreign key.
`leads.services.convert_lead_to_account(lead_obj, request, create_opportunity=True)`
(`backend/leads/services.py`) runs as a side effect when a lead's `status` is updated to
`"converted"` through the normal lead update views (`backend/leads/views/lead_views.py`). There is
no dedicated "convert" endpoint. It:

1. `get_or_create`s an `Account` (case-insensitive name match within the org).
2. `get_or_create`s a `Contact` from the lead's email, if the lead has one, and links it to the
   account both by direct FK and via `Account.contacts`.
3. Creates an `Opportunity` if requested and the lead carries enough data to justify one, copying
   `assigned_to`/`teams`/`tags` across.
4. Reassigns the lead's `Comment`/`Attachments` rows (generic-relation `ContentType`/`object_id`
   pairs) from the lead onto the new account.
5. Sets `lead_obj.status = "converted"`.

There is deliberately no persisted link back from the created `Account`/`Contact`/`Opportunity` to
the originating `Lead`, `Lead`'s own model file documents this: earlier `converted_account`/
`converted_contact`/`converted_opportunity`/`conversion_date` fields were removed because they were
"never populated, conversion just sets status." If you need to trace a record back to the lead it
came from, there's nothing in the schema to follow; only the account/contact name or email is
likely to match.

`Contact` and `Account` are linked two separate ways at once: `Contact.account` is a nullable
foreign key (`on_delete=SET_NULL`), and `Account.contacts` is an independent many-to-many. Lead
conversion populates both. Code that needs "the contacts belonging to this account" should decide
deliberately which of the two it means, rather than assuming they're always in sync.

`Opportunity.account` is a nullable FK to `Account`; `Opportunity.contacts` is a many-to-many to
`Contact`. `Case.account` is a required-in-practice (blank/null allowed, but `on_delete=CASCADE`)
FK to `Account`, and `Case.contacts` is a many-to-many to `Contact`.

## Fulfillment

`backend/fulfillment/` takes a sale from paid to dispatched for a store that sells through several
channels. Orders and fulfillment share `catalog.SalesChannel`, so the imported order, invoice and
fulfillment sale refer to the same channel identity. Fulfillment links to `orders.Order` through
`Sale.order` and to `OrderLineItem` through `PreparationOrder.line_item`; the reverse accessors (`order.sale`,
`order_line_item.preparation_orders`) change no table.

```
  orders.Order ◄──1:1── Sale ──► catalog.SalesChannel (late penalty)
                       │
                       ├──── PreparationOrder (0..n) ──► orders.OrderLineItem (optional)
                       ├──── PackagingOrder (1)
                       └──── DispatchOrderItem (0..1) ──► DispatchOrder ──► DeliveryOption
                                                        (one run)          │
                                                                           └── DeliveryOptionDay (0..n)
```

- **`DeliveryOption`** is configuration, written by admins. Its channel points to the shared
  `catalog.SalesChannel`.
- **`Sale`** carries the committed delivery time. Its channel also points to the same
  `catalog.SalesChannel` recorded on an order.
- **`PreparationOrder`** and **`PackagingOrder`** are minimal placeholders. Nothing in the codebase
  had them, and dispatch needs to know when a sale is packed, so this app defines just enough: a
  status and a completion time, packing refused while a preparation is pending. They are expected
  to be reconciled with the fuller versions at the merge. The seam is narrow: dispatch reads only
  "packaging order completed" (`services.ready_sales`), the queues read statuses
  (`queues.work_queue`), and every change of state goes through `services.complete_preparation`
  and `services.complete_packaging`, so a replacement has two functions and two queries to honour.
- **Inspection** (returned products reviewed before they go back into inventory) is an order type
  without a model here: the returns project owns the orders and their queue. The type exists so a
  team can be assigned to it, and that project checks access with
  `fulfillment.access.may(profile, "INSPECTION", level)`.
- **`QueueAssignment`** gives a `common.Teams` team `VIEW`, `HANDLE` or (delivery only) `CLOSE`
  on one of the four order types. It reuses the org's existing teams rather than adding a group
  model, and every rule reading it lives in `fulfillment/access.py`. A queue belongs to its type,
  not to a team: everyone with access to the type shares it.
- **Workdays** belong to each `DeliveryOption`: its weekly opening times (`monday_opens` to
  `sunday_opens`) and its **`DeliveryOptionDay`** rows, dated free days and extra working days. A
  new option may copy the org's `business_hours.BusinessCalendar` once, at creation
  (`services.import_business_hours`); planning never reads the business calendar, so the two
  evolve independently.
- **`DispatchOrder`** is one run of one option (unique per option and departure time), and
  **`DispatchOrderItem`** puts one sale on it (a sale is on at most one). Its `mode` says who
  decided it: `AUTO` orders are written only by `services.create_dispatch_orders`, which
  recalculates from the current values on demand; `MANUAL` and `HYBRID` orders by
  `services.create_manual_dispatch_order`, and a recalculation keeps their sales and run.
  `services.release_dispatch_order` undoes an open one and `services.close_dispatch_order` fixes
  the actual figures. All take a per-org advisory lock, so no two of them interleave.
- **The plan** is `fulfillment/planner.py`, pure Python with no ORM, so its rules are tested on
  their own; the services translate rows in and out of it. The rules are in
  [Dispatch](../features/dispatch.md).

All fulfillment tables are org-scoped `BaseOrgModel`s under RLS (`fulfillment/0002_enable_rls.py`).
The shared sales channel is `catalog_sales_channel`, under Catalog's RLS policy.

## Conventions

Nearly every model in this codebase inherits `BaseModel` (`backend/common/base.py`), which via
`AuditModel` (`backend/common/mixins.py`) provides:

- `id`: a `UUIDField` primary key (`default=uuid.uuid4`), not an auto-incrementing integer.
- `created_at`/`updated_at`: set automatically (`auto_now_add`/`auto_now`).
- `created_by`/`updated_by`: foreign keys to **`User`**, not `Profile`, set from
  `crum.get_current_user()` in `BaseModel.save()`. This is worth being precise about: a permission
  check that compares `request.profile` directly to `some_object.created_by` is comparing a
  `Profile` instance to a `User` foreign key and will always be `False`. See
  [Permissions and roles](permissions-and-roles.md#object-level-checks) for the bug class that
  causes and how to compare them correctly (`profile.user_id == obj.created_by_id`).

Three models opt out of this entirely and inherit plain `models.Model` instead: `User`
(`backend/common/models.py:34`), `MagicLinkToken` (`:524`), and `PortalAccessToken` (`:910`). All
three still declare their own `id = models.UUIDField(default=uuid.uuid4, primary_key=True)` by
hand, so the UUID-primary-key convention holds even for them, but none of the three has
`created_at`/`updated_by`/`created_by` fields, which makes sense for what they are: `User` predates
and sits outside the org-membership model entirely (`Profile` is what's org-scoped), and
`MagicLinkToken`/`PortalAccessToken` are short-lived, auth-bootstrap rows with no meaningful
"who last touched this" to record.

Org-scoped models add one more field: an `org` foreign key to `common.Org`, either inherited from
`BaseOrgModel` or (the more common pattern in this codebase) declared by hand on top of
`BaseModel`. See [Multi-tenancy and RLS](multi-tenancy-and-rls.md#baseorgmodel) for which models do
which and why both are valid as long as the table is also registered and migrated for RLS.

**Every record type with a board has per-org stages, but `Opportunity.stage` is stored differently
from the other three.** `Lead.stage`, `Case.stage`, and `Task.stage` are nullable foreign keys to a
per-org `LeadStage`/`CaseStage`/`TaskStage` row (itself belonging to a `LeadPipeline`/`CasePipeline`/
`TaskPipeline`). `Opportunity` has a `pipeline` foreign key to a `DealPipeline` and a `stage`
`CharField` holding one of that pipeline's `DealStage.code` values, so the value on the wire is a
stable string (`PROSPECTING`, `CLOSED_WON`, or a code an admin's stage was given) rather than a row
id; the code never changes once the stage exists, while its label can. What a deal's stage means
(open, won, lost) is the `DealStage.kind`, which is what reports and goals count by. All three of the
other models also keep a separate, fixed-choices `status` field (`Lead.status`, `Case.status`,
`Task.status`) alongside the configurable `stage` FK. A stage's `maps_to_status` field is what keeps
the two in sync when a record moves between stages. A deal has no second field of that kind: its
stage's kind already says whether it is open, won or lost.

Several models (`Lead`, `Opportunity`, `Case` among them) carry a `custom_fields = JSONField()`
whose values are validated against `common.CustomFieldDefinition`, a per-org schema extension
mechanism that doesn't require a migration to add a field for one org.

Finally, one validation gotcha worth knowing before you rely on `Model.clean()`: DRF's
`ModelSerializer` calls `instance.save()`, not `instance.full_clean()`, so a model's `clean()`
method is invoked through the API only if that model's own `save()` explicitly calls
`self.full_clean()` itself: most don't. `Lead.clean()`, `Opportunity.clean()`, and `Case.clean()`
(`backend/cases/models.py:180`) all define real validation rules this way, and by default those
rules are silently unenforced through the API, because none of `Lead.save()`, `Opportunity.save()`,
or `Case.save()` calls `full_clean()`. A handful of other models do call it from their own
`save()`, so their `clean()` rules reach the API as a Django
`ValidationError`: a 500 unless the view happens to catch it, not a clean DRF 400: `Task.save()`
(`backend/tasks/models.py:403`), and `Comment.save()`/`Attachments.save()`
(`backend/common/models.py:316-318` and `:430-432`, both there specifically to enforce that a
comment or attachment's `org` matches the org of whatever record it's attached to). There's no
blanket rule for which models do this, check the individual `save()` method rather than assuming.
Where an unenforced `clean()` rule turned out to matter through the API, it's been reimplemented
directly in the serializer instead of relying on `full_clean()`, `opportunity/serializer.py`'s
`validate()` duplicates the two rules from `Opportunity.clean()` for exactly this reason, per its
own comment. If you add a `clean()` method to a model, verify whether that model's `save()` calls
`full_clean()` before assuming the rule is enforced anywhere outside a unit test that calls
`full_clean()` directly.

## Where an org came from

`Org.signup_source` records, once, how the org's creator arrived, for growth analytics. It is never
read by a permission check or a queryset filter.

- The web app's sign-in page stores the first visit's `utm_source`, `utm_medium` and
  `utm_campaign` tags and the referring site's host name in an httpOnly `signup_source` cookie
  (30 days). An existing cookie is kept, so the first visit wins. Google's redirect back to the
  sign-in page and the app's own pages are never taken as a source.
- Creating an org sends that value with `POST /api/org/` as `signup_source`, and the cookie is
  then deleted.
- The API keeps only those four keys, as short printable text and a bare host name (never a full
  URL, whose path or query can carry tokens), and adds `client`: `phone` for the mobile app,
  `web` otherwise. A malformed value is dropped and never blocks the create.

Nothing personal is stored: no address, no email and no full referring URL.
