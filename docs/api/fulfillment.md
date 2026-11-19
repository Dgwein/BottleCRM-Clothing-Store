# Fulfillment

Sales channels, delivery options, sales with their preparation and packaging orders, dispatch
orders and the dispatch plan, the queues, and which teams handle which order type. What the rules
are and how the plan is chosen is in [Dispatch](../features/dispatch.md). Routes are in
`backend/fulfillment/urls.py`, views in `fulfillment/views.py`, every change of state goes through
`fulfillment/services.py`, the permission rule is `fulfillment/access.py`, the queues
`fulfillment/queues.py`, and the plan is computed by `fulfillment/planner.py`.

| Endpoint | Does |
| --- | --- |
| `GET /api/fulfillment/channels/` | The org's channels, by name |
| `POST /api/fulfillment/channels/` | Add one (`201`) |
| `GET /api/fulfillment/channels/{id}/` | One channel |
| `PATCH /api/fulfillment/channels/{id}/` | Change one |
| `DELETE /api/fulfillment/channels/{id}/` | Delete one (`204`); `409` while a sale or option uses it |
| `GET /api/fulfillment/delivery-options/` | The org's delivery options, by name |
| `POST /api/fulfillment/delivery-options/` | Add one (`201`) |
| `GET /api/fulfillment/delivery-options/{id}/` | One option |
| `PATCH /api/fulfillment/delivery-options/{id}/` | Change one |
| `DELETE /api/fulfillment/delivery-options/{id}/` | Delete one (`204`); `409` while a dispatch order uses it |
| `GET /api/fulfillment/delivery-options/{id}/days/` | The option's free and extra working days, by date |
| `POST /api/fulfillment/delivery-options/{id}/days/` | Add one (`201`) |
| `DELETE /api/fulfillment/delivery-options/{id}/days/{day_id}/` | Remove one (`204`) |
| `GET /api/fulfillment/sales/` | Sales, newest first; paged |
| `POST /api/fulfillment/sales/` | Register a sale for an order (`201`) |
| `GET /api/fulfillment/sales/{id}/` | One sale with its preparation and packaging orders and its dispatch |
| `POST /api/fulfillment/preparation-orders/{id}/complete/` | Complete a preparation order; answers the sale |
| `POST /api/fulfillment/packaging-orders/{id}/complete/` | Complete the packaging order, making the sale ready to dispatch; answers the sale |
| `GET /api/fulfillment/dispatch-plan/` | Preview the plan from the current values; changes nothing |
| `GET /api/fulfillment/dispatch-orders/` | Dispatch orders; `status=` and the delivery filters; paged |
| `POST /api/fulfillment/dispatch-orders/` | Create or recalculate the automatic dispatch orders |
| `POST /api/fulfillment/dispatch-orders/manual/` | Create a manual or hybrid dispatch order from chosen sales (`201`) |
| `GET /api/fulfillment/dispatch-orders/{id}/` | One dispatch order with its sales |
| `DELETE /api/fulfillment/dispatch-orders/{id}/` | Release an open dispatch order (`204`); its sales are ready again |
| `POST /api/fulfillment/dispatch-orders/{id}/close/` | Close one: compute its actual figures and fix them |
| `GET /api/fulfillment/queues/` | The queues the caller's teams may see, with their level and how many orders wait |
| `GET /api/fulfillment/queues/preparation/` | The preparation queue; filtered and paged |
| `GET /api/fulfillment/queues/packaging/` | The packaging queue; filtered and paged |
| `GET /api/fulfillment/queues/delivery/` | Open dispatch orders, and how many ready sales are on none; filtered and paged |
| `GET /api/fulfillment/queue-assignments/` | Which teams handle which order types (admins) |
| `POST /api/fulfillment/queue-assignments/` | Assign a team to an order type (`201`, admins) |
| `PATCH /api/fulfillment/queue-assignments/{id}/` | Change its level (admins) |
| `DELETE /api/fulfillment/queue-assignments/{id}/` | Remove it (`204`, admins) |

## Who may

Working an order belongs to the teams assigned to its type (see
[Dispatch: teams, queues and permissions](../features/dispatch.md#teams-queues-and-permissions)).
Every check is on the caller's level for that type, the highest any of their teams holds:

| Call | Needs |
| --- | --- |
| Complete a preparation or packaging order | `HANDLE` on that type |
| The preparation or packaging queue | `VIEW` on that type |
| `GET` dispatch orders, one dispatch order, the plan, the delivery queue | `VIEW` on delivery |
| Create, recalculate or release dispatch orders | `HANDLE` on delivery |
| Close a dispatch order | `CLOSE` on delivery |
| `GET` sales | any level on any type |
| Channels, delivery options and their days: read | every member |
| Channels, delivery options, their days and queue assignments: write; queue assignments: read | an admin who is a person (`may_configure`); app members may read assignments |
| Register a sale | an admin (`is_org_admin`), app members included |

Inspection is an order type teams can be given; its orders and queue are served by the returns
project, which asks `fulfillment.access.may(profile, "INSPECTION", level)`.

Org admins hold every level without being on a team. A caller without the level gets `403` with
`{"detail": "None of your teams may <see|work|close> <type> orders."}`, before the record is looked
up. Another org's record, and an id that does not exist, both answer `404`. An id of another org's
order, channel, sale, team or order line in a body answers `400` as if it did not exist.

Tokens use the `fulfillment` resource: `fulfillment:read` for every `GET`, `fulfillment:write` for
the rest (see [Tokens and API keys](tokens-and-api-keys.md)). A token acts with its owner's levels.

## Paging

Every list here that grows with the org's work is paged with `limit` (50 by default, at most 200)
and `offset`, and answers its rows with `count` (all matching rows), `limit` and `offset`. A
`limit` or `offset` that is not a non-negative whole number falls back to its default.

## Channels

```json
{
  "id": "4f1c...",
  "name": "Mercado Libre",
  "late_penalty": "100.00",
  "created_at": "2026-11-18T09:00:00-06:00",
  "updated_at": "2026-11-18T09:00:00-06:00"
}
```

`name` is unique in the org, ignoring case. `late_penalty` is `0` or more, in the org's currency.

## Delivery options

```json
{
  "id": "9b2e...",
  "name": "Mercado Libre pickup",
  "kind": "CHANNEL_PICKUP",
  "channel": "4f1c...",
  "channel_name": "Mercado Libre",
  "location": "Store, back door",
  "weekday": "TUE",
  "departure_time": "11:00:00",
  "dispatch_minutes": 0,
  "cost": "45.00",
  "capacity": 30,
  "is_active": true,
  "monday_opens": "09:00:00",
  "tuesday_opens": "09:00:00",
  "wednesday_opens": "09:00:00",
  "thursday_opens": "09:00:00",
  "friday_opens": "09:00:00",
  "saturday_opens": null,
  "sunday_opens": null,
  "created_at": "2026-11-18T09:00:00-06:00",
  "updated_at": "2026-11-18T09:00:00-06:00"
}
```

- `kind`: `DOOR_TO_DOOR`, `CHANNEL_PICKUP`, `CHANNEL_AGENCY` or `COMMON_DISPATCH`. The two channel
  kinds need a `channel` (`400` otherwise); the others may leave it `null` to serve every channel.
- `weekday`: `MON` to `SUN`, or `""` for every day. `departure_time`: `HH:MM[:SS]` in the org's
  timezone, or `null` for any time.
- `dispatch_minutes`: `0` up to 60 days.
- `capacity`: the most packages one run takes, `1` or more, or `null` for no limit.
- `monday_opens` to `sunday_opens`: the option's working week, each weekday's opening time in the
  org's timezone, or `null` for a day off. Every day `00:00:00` unless given.
- `import_business_hours` (write-only, on create): `true` copies the org's business hours onto the
  new option: each weekday's opening time (`null` where the business is closed) and every holiday
  from today on as a free day. `400` when the org has no business hours, and on an update. After
  that the option's days are its own.

An option's dated days are managed under `/api/fulfillment/delivery-options/{id}/days/`:

```json
{"id": "...", "date": "2026-12-25", "kind": "FREE", "opens_at": null, "note": "Christmas"}
```

`kind` is `FREE` (the option does not run that day) or `WORKING` (it runs although its week says
it does not, opening at `opens_at`, midnight when `null`). A free day takes no `opens_at` (`400`),
and a date has one setting per option (`400` for a second).

## Sales

Register one with:

```json
{
  "order": "e7a0...",
  "channel": "4f1c...",
  "committed_delivery_at": "2026-11-20T12:00:00-06:00",
  "preparations": [
    {"description": "Sew the hem", "line_item": "c1d2..."},
    {"description": "Print the name"}
  ]
}
```

`preparations` is optional; a `line_item` must be on that order. The order must not be cancelled or
already have a sale (`400`). The sale comes back with its packaging order:

```json
{
  "id": "5a9d...",
  "order": "e7a0...",
  "order_name": "Sale 1001",
  "order_number": "",
  "order_status": "ACTIVATED",
  "channel": "4f1c...",
  "channel_name": "Mercado Libre",
  "committed_delivery_at": "2026-11-20T12:00:00-06:00",
  "preparation_orders": [
    {"id": "...", "line_item": "c1d2...", "description": "Sew the hem", "status": "PENDING", "completed_at": null}
  ],
  "packaging_order": {"id": "...", "status": "PENDING", "completed_at": null},
  "dispatch": null,
  "created_at": "2026-11-18T09:00:00-06:00"
}
```

`dispatch` is `null` until the sale is on a dispatch order, then:

```json
{
  "dispatch_order": "0c3b...",
  "status": "OPEN",
  "delivery_option_name": "Mercado Libre pickup",
  "departs_at": "2026-11-24T11:00:00-06:00",
  "estimated_delivery_at": "2026-11-24T11:00:00-06:00",
  "late": true,
  "cost_share": "22.50",
  "penalty": "100.00"
}
```

While `status` is `OPEN` these are estimates; once it is `CLOSED` they are the sale's actual
figures.

Completing a preparation or packaging order that is already completed answers `409`, and so does
completing packaging while a preparation order is pending, or when the order was cancelled. The
`409` body is `{"detail": "<why>"}`. Completing packaging creates no dispatch order.

## Dispatch orders

One run of one delivery option, with the sales on it:

```json
{
  "id": "0c3b...",
  "delivery_option": "9b2e...",
  "delivery_option_name": "Mercado Libre pickup",
  "kind": "CHANNEL_PICKUP",
  "location": "Store, back door",
  "capacity": 30,
  "status": "OPEN",
  "mode": "AUTO",
  "departs_at": "2026-11-24T11:00:00-06:00",
  "cost": "45.00",
  "items": [
    {
      "id": "...",
      "sale": "5a9d...",
      "order": "e7a0...",
      "order_name": "Sale 1001",
      "channel": "4f1c...",
      "channel_name": "Mercado Libre",
      "committed_delivery_at": "2026-11-20T12:00:00-06:00",
      "estimated_delivery_at": "2026-11-24T11:00:00-06:00",
      "late": true,
      "cost_share": "22.50",
      "penalty": "100.00"
    }
  ],
  "closed_at": null,
  "created_at": "2026-11-18T10:05:00-06:00",
  "updated_at": "2026-11-18T10:05:00-06:00"
}
```

`mode` is `AUTO` (written by the plan), `MANUAL` (sales a person chose) or `HYBRID` (chosen sales,
the rest of the run filled by the plan).

### Creating them

`POST /api/fulfillment/dispatch-orders/` creates or recalculates the automatic orders. Its body is
optional: `{"allow_non_working_days": false}`. It plans every ready sale not on a manual or hybrid
order and writes the plan as open automatic orders: an open automatic order of the same run is kept
and updated, a new run gets a new one, and an automatic order the plan no longer uses is deleted.
A sale the plan cannot place, or that is no longer ready, is taken off its automatic order. Manual
and hybrid orders keep their sales and run; their figures are recomputed from the current values.
Closed orders are never touched. It answers:

```json
{
  "generated_at": "2026-11-18T10:05:00-06:00",
  "currency": "USD",
  "exhaustive": true,
  "dispatch_orders": ["<every open dispatch order, as above>"],
  "unassigned": ["<sales no run can take, as in the plan>"]
}
```

`POST /api/fulfillment/dispatch-orders/manual/` creates one order from chosen sales and answers it
(`201`):

```json
{
  "mode": "HYBRID",
  "sales": ["5a9d...", "77de..."],
  "delivery_option": "9b2e...",
  "allow_non_working_days": false
}
```

- `mode`: `MANUAL` or `HYBRID`. `HYBRID` then fills the run's remaining capacity with the other
  ready sales the plan finds it best for.
- `sales`: at least one, each once. Each must be ready to dispatch and not on another manual or
  hybrid order (`409` naming the ones that are not).
- `delivery_option`: optional. With it, the sales leave on its next free run; without, on the one
  run that carries them all at the lowest cost and penalties. Either way the option must serve
  every chosen sale's channel and have room for all of them (`409` otherwise), and be active (`409`).

Chosen sales leave the automatic orders they were on, whose cost is split again between the rest.
An automatic order on the same run gives it up, and the automatic orders are then recalculated at
once, keeping to working days, so the sales it carried are planned again.

`DELETE /api/fulfillment/dispatch-orders/{id}/` releases an open order of any mode: it is deleted
and its sales are ready again. A closed order answers `409`.

### Closing them

`POST /api/fulfillment/dispatch-orders/{id}/close/` takes no body and answers the closed order.
An option with a delivery time closes once that time has passed and keeps it as the departure;
one without closes when executed, leaving now. Closing recomputes the order's figures from the
current values (option cost, dispatch minutes, penalties), takes off sales whose order was
cancelled, and fixes the result. It answers `409` before a timed option's delivery time, when the
order is already closed, when its option was switched off, when the option's capacity is now below
the number of sales on it, or when no sale is left on it.

### Workdays

Each option's runs leave on its own working days: its week, less its free days, plus its extra
working days. `allow_non_working_days` (a body field on the two `POST`s,
`?allow_non_working_days=true` on the plan) lets that request's runs leave on any day. Any value
other than `true` or `false` answers `400`.

## Queues

```json
{"queues": [{"order_type": "PACKAGING", "access": "HANDLE", "pending": 3}]}
```

Only the types this app serves and the caller may see are listed: `PREPARATION`, `PACKAGING` and
`DELIVERY`. A queue is shared by everyone with access to its type, whatever their team. A work
queue answers the orders ready to work:

```json
{
  "order_type": "PREPARATION",
  "items": [
    {
      "id": "<the preparation order>",
      "sale": "5a9d...",
      "order": "e7a0...",
      "order_name": "Sale 1001",
      "channel": "4f1c...",
      "channel_name": "Mercado Libre",
      "committed_delivery_at": "2026-11-20T12:00:00-06:00",
      "description": "Sew the hem",
      "line_item": "c1d2...",
      "created_at": "2026-11-18T09:00:00-06:00"
    }
  ],
  "count": 1,
  "limit": 50,
  "offset": 0
}
```

`description` and `line_item` are a preparation order's; packaging items have `""` and `null`.

Work queue filters:

| Parameter | Keeps |
| --- | --- |
| `channel` | Sales of this channel; repeatable |
| `due_from`, `due_to` | Sales whose committed delivery falls on or after, on or before this day (`YYYY-MM-DD`, the org's day) |
| `search` | Sales whose order name or number contains this, ignoring case |
| `ordering` | `due` (earliest committed delivery first, the default), `-due`, `created`, `-created` |

The delivery queue answers `{"dispatch_orders": [...], "count": n, "limit": n, "offset": n,
"unplanned_sales": n}`, where `unplanned_sales` counts ready sales on no dispatch order. It and
`GET /api/fulfillment/dispatch-orders/` take the delivery filters:

| Parameter | Keeps |
| --- | --- |
| `delivery_option` | Orders on this option; repeatable |
| `channel` | Orders carrying a sale of this channel; repeatable |
| `mode` | `AUTO`, `MANUAL` or `HYBRID`; repeatable |
| `departs_from`, `departs_to` | Orders leaving on or after, on or before this day |
| `search` | Orders carrying an order whose name or number contains this |
| `ordering` | `departs` (the default) or `-departs` |

A malformed id or date answers `400`; an unknown `ordering`, `mode` or `status` is ignored.

## Queue assignments

```json
{
  "id": "...",
  "team": "<team id>",
  "team_name": "Packers",
  "order_type": "PACKAGING",
  "access": "HANDLE",
  "created_at": "2026-11-18T09:00:00-06:00"
}
```

`order_type` is `PREPARATION`, `INSPECTION`, `PACKAGING` or `DELIVERY`; `access` is `VIEW`,
`HANDLE` or `CLOSE`, and `CLOSE` only for `DELIVERY` (`400` otherwise). A team has one assignment
per type (`400` for a second).

## The plan

A preview of what `POST /api/fulfillment/dispatch-orders/` would write; it changes nothing.

```json
{
  "generated_at": "2026-11-18T10:05:00-06:00",
  "currency": "USD",
  "exhaustive": true,
  "total_cost": "100.00",
  "total_penalty": "0.00",
  "total": "100.00",
  "departures": [
    {
      "delivery_option": {"id": "...", "name": "Common dispatch", "kind": "COMMON_DISPATCH", "location": "", "cost": "100.00", "capacity": null},
      "departs_at": "2026-11-20T16:00:00-06:00",
      "arrives_at": "2026-11-21T16:00:00-06:00",
      "sales": ["5a9d...", "77de..."]
    }
  ],
  "assignments": [
    {
      "sale": "5a9d...",
      "order": "e7a0...",
      "order_name": "Sale 1001",
      "channel": "4f1c...",
      "channel_name": "Mercado Libre",
      "committed_delivery_at": "2026-11-22T12:00:00-06:00",
      "delivery_option": "...",
      "departs_at": "2026-11-20T16:00:00-06:00",
      "arrives_at": "2026-11-21T16:00:00-06:00",
      "late": false,
      "cost_share": "50.00",
      "penalty": "0.00"
    }
  ],
  "unassigned": []
}
```

- `departures`: one per run used, by time. An option runs at most once a day, and can appear more
  than once when its capacity sends sales to later days.
- `unassigned`: sales no run can take, with the same sale fields as an assignment.
- `exhaustive`: `false` when the search stopped at its work limit: this is the best plan found,
  or, if it found none, each sale on its cheapest run with room.
