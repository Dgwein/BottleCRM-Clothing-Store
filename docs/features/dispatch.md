# Dispatch

For a store that sells through several channels (the counter, Mercado Libre, Amazon and more), a
sale is not finished when it is paid: the products may have to be made ready, the sale has to be
packed, and the package has to leave on one of the ways it can reach the buyer. This page covers
that path, how the run for each package is chosen, and which teams do what. The code is
`backend/fulfillment/`; the endpoints are in [Fulfillment](../api/fulfillment.md), and how the app
sits next to orders is in [Data model](../architecture/data-model.md#fulfillment).

## A sale's path

```
order ──► sale ──► preparation orders ──► packaging order ──► ready to dispatch
                   (0 or more)             (1)                       │
                                                                     │ on demand
                                                                     ▼
                                     dispatch order (one run of one option, several sales)
                                     open: recalculated on request
                                     closed: actual figures fixed
```

- A **sale** extends one `Order` with the two things fulfilment needs: the **channel** it came
  through and the **committed delivery time** promised to the buyer. `Order`, `Account` and
  `Product` are not changed; the channel sync writes orders as it always has and registers a sale
  for each.
- A **preparation order** is work that makes a product ready before it can be packed (sewing,
  printing a name). A sale has none when everything is in stock. It may name the order line it is
  for.
- Every sale has one **packaging order**. It cannot be completed while a preparation order is
  still pending. A completed one makes the sale **ready to dispatch**; it creates nothing else.
- A **dispatch order** is one run of one delivery option and the sales leaving on it, created on
  demand (see [Creating dispatch orders](#creating-dispatch-orders)).
- **Closing** a dispatch order confirms it. Until then its figures are estimates. Closing computes
  them once more from the values in force and fixes them, and only then are they the sale's actual
  figures.

An order that is cancelled is not packed, planned or closed on a dispatch order.

**Inspection** is a fourth order type, for returns: a returned product is reviewed before it goes
back into inventory. Inspection orders and their queue belong to the returns project; this app
only lets teams be given access to the type (see [Teams](#teams-queues-and-permissions)).

Preparation and packaging orders are minimal here, to be reconciled at the merge with the projects
that own them.

## Channels and delivery options

A **channel** is the shared `catalog.SalesChannel` used by catalog, orders and invoices. It carries
the **late penalty**: what the store pays when a sale from that channel reaches the buyer after its
committed time. It belongs to the channel, not the sale, and may be `0` (a sale at the counter,
say).

A **delivery option** is one way a package can leave:

| Field | Meaning |
| --- | --- |
| `kind` | `DOOR_TO_DOOR`, `CHANNEL_PICKUP` (the channel collects it), `CHANNEL_AGENCY` (dropped at the channel's agency) or `COMMON_DISPATCH` |
| `channel` | Only this channel's sales may use it; blank serves every channel. Required for the two channel kinds |
| `weekday`, `departure_time` | The delivery day and time: when it leaves, in the org's timezone. Either may be blank: a blank weekday is every day, a blank time is any time that day, and both blank (door to door, an agency drop) means it leaves when its run is executed, which is when its dispatch order is closed |
| `dispatch_minutes` | From leaving until the commitment is met. It may be `0`, for instance when what the sale committed to is the handover itself |
| `location` | Where it is handed over |
| `cost` | Paid once per run and split evenly between the sales on it |
| `capacity` | The most packages one run can take; blank has no limit |
| `is_active` | Only active options are available. Switch one off rather than deleting it |
| `monday_opens` ... `sunday_opens` | The option's working week: when each weekday opens, or blank for a day it does not work. Every day from midnight until changed (see [Workdays](#workdays)) |

An option runs at most once a day. A run on a different day or time is a separate option: a pickup
on Tuesdays and Fridays at 11:00 is two options, each with its own cost.

What the committed time means is the channel's business, and the options follow it. A marketplace
may commit the store to hand the package to its agency by a set time, so an agency option with
`dispatch_minutes` of the trip there fits; a door-to-door sale commits to the buyer's door, so the
courier's `dispatch_minutes` is the whole delivery.

## Workdays

Runs leave on working days, and each delivery option has its own: a carrier, an agency and the
channel's pickup each keep their own week and their own days off.

- **The working week** is the option's opening time for each weekday (`monday_opens` to
  `sunday_opens`); a weekday without one is a day off. A new option works every day from midnight.
- **Free days** are dates it does not run, such as a holiday.
- **Extra working days** are dates it runs although the week says it does not, opening at their
  own time (midnight when none is given).

A new option can **import the general settings**: it copies the org's business hours (Settings ›
Business hours), each weekday's opening time and every holiday from today on as a free day. From
then on its days are its own: changing the business hours later does not touch it, and its week,
free days and working days are edited on the option.

A timed run that would fall on a day off moves to the option's next run (a Friday pickup on a free
Friday waits a week); a run with no time leaves on the option's next working day, when that day
opens. A request to plan or to create dispatch orders may set `allow_non_working_days`, and then its
runs may leave on any day.

## How the plan is chosen

The plan covers every ready sale not held by a person's dispatch order (see below), computed from
the moment it is asked for and the values in force then.

1. **Runs.** Each active option offers its next run on one of its working days at or after now. An option with
   no day or time runs once a day too: today, leaving now, or on its next working day once today's
   run is taken, and it offers only that one run. An option with a day or time and a capacity also
   offers the runs after it, as many as it would take to carry every sale it serves on its own,
   so sales still find a seat when one option after another is fully booked; no plan ever needs
   more. A day whose run is closed, or held by a person's order, is taken.
2. **Arrival** is the departure plus `dispatch_minutes`.
3. **Availability.** A sale may use the runs of active options that serve its channel, each up to
   its capacity. A sale no run can take (none serves its channel, or every one that does is full)
   is listed as unassigned.
4. **Penalty.** A sale is late on a run that arrives after its committed time, and then pays its
   channel's penalty. Arriving exactly on time is on time.
5. **Cost and grouping.** A run's cost is split evenly between the sales on it, in cents, so the
   shares add up to the cost exactly. The penalty stays with the sale it belongs to.
6. **Profitability.** However the cost is split, the total is the cost of every run used plus every
   penalty. The plan places as many sales as it can, then has the lowest total, then the fewest
   late sales. Fewer runs, then earlier arrivals, break any remaining tie.

The search is exact on those three (branch and bound over the runs, with a min-cost flow for
capacities). On an org with an unusual number of options and sales it stops after a fixed amount of
work (a second or two) and returns the best plan found, or, when it found none by then, places each
sale on its cheapest run with room, those whose lateness costs most first. Either way `exhaustive`
is `false`.

### An example

It is Wednesday 09:00. Two sales are packed, one from Mercado Libre (penalty 100) and one from
Amazon (penalty 80), both due on Sunday. There are three options:

| Option | Serves | Leaves | Takes | Cost |
| --- | --- | --- | --- | --- |
| Mercado Libre agency | Mercado Libre | any time | 30 minutes | 70 |
| Amazon agency | Amazon | any time | 30 minutes | 70 |
| Common dispatch | everyone | Fridays 16:00 | one day | 100 |

Each sale's own cheapest option costs 70, 140 in all. Together they fill the common dispatch for
100, 50 each, and both arrive on Saturday, in time. That is the plan.

Now a third Mercado Libre sale is packed, due in an hour. Only the Mercado Libre agency gets it
there in time, so that agency leaves anyway, and the other Mercado Libre sale joins it: 35 each.
The Amazon sale alone would pay 100 for the common dispatch, so it goes to its own agency for 70.
The plan costs 140, against 170 for keeping the common dispatch.

A penalty can be the cheaper choice. A sale whose channel charges 50 for being late, with a
courier at 100 that arrives in time and a common dispatch at 20 that does not, goes on the common
dispatch (70 instead of 100). With a penalty of 200 it goes with the courier.

Capacity changes the picture when a run fills up. A courier leaving daily at 16:00 for 60, taking
two packages, with three sales due in three days: two leave today for 30 each, and the third leaves
tomorrow and pays the whole 60.

## Creating dispatch orders

Nothing is created when a sale is packed. A person on a delivery team asks, in one of three ways:

- **Automatic.** Every ready sale not held by a person's order is planned by the rules above and
  written as open automatic dispatch orders, one per run used. Asking again recalculates: an open
  automatic order of the same run is kept and updated, so a new sale joins the run it fits and the
  cost is split again between everyone on it; a sale may move to another run, and an automatic
  order the plan no longer uses is deleted. A missed run moves on by itself: its sales go to the
  next one.
- **Manual.** The person chooses the sales, and optionally the option. They all leave on one run:
  the option's next free run, or, with no option named, the run that carries them all at the lowest
  cost and penalties. The option must serve every chosen sale's channel and have room for all of
  them. Chosen sales leave any automatic order they were on, whose cost is split again between who
  is left. An automatic order on the same run gives the run up, and the sales it carried are
  planned again at once: the automatic orders are recalculated, keeping to working days.
- **Hybrid.** Manual, and then the plan fills the run's remaining capacity from the other ready
  sales. The run's cost is paid already, so the plan puts on it the sales it is best for, taking it
  first whenever it costs them no more, and leaves off a sale that would be late on it when
  another run gets it there for less.

Manual and hybrid orders are a person's decision: an automatic recalculation never moves their
sales or their run, and only refreshes their figures from the current values. An open order of any
mode can be **released**, which deletes it and makes its sales ready again.

## Closing

A dispatch order closes after its delivery time when the option has one (it left then), and when
it is executed when the option has none (it leaves now); closing a timed one before its time is
refused. Closing computes the actual figures from the values in force at that moment: the cost is
the option's cost now, split between its sales, and each sale that arrives late pays its channel's
penalty now. A sale whose order was cancelled is taken off first. Closing is also refused when the
option has been switched off since, or now takes fewer packages than the order holds. Closed
figures never change again, and that day's run of the option is spent, spare capacity or not.

Between an automatic order's delivery time and its closing it is still open, and a recalculation
treats the run as missed and moves its sales to the next one. Close a run that left before
recalculating.

## Teams, queues and permissions

Handling an order belongs to the teams assigned to its type. The four types are **preparation**,
**inspection** (returns, handled by the returns project), **packaging** and **delivery** (the
dispatch orders). An admin assigns a team (the Team page, `/api/teams/`) to a type with one of three
levels, each including the ones before it:

| Level | Preparation, inspection, packaging | Delivery |
| --- | --- | --- |
| `VIEW` | See the type's queue and orders | See the queue, the dispatch orders and the plan |
| `HANDLE` | Also complete the orders | Also create, recalculate and release dispatch orders |
| `CLOSE` | (not offered) | Also close dispatch orders, which fixes their money |

A member holds the highest level any of their teams holds for a type, and loses it the moment they
leave the team. A member on no team does no fulfilment work. Sales, with all their orders, are open
to anyone with any level. Org admins hold every level without a team; so do app members, which is
how the channel sync registers sales.

Each type has one **queue**, shared by everyone with access to the type, whatever their team: two
teams given packaging work the same packaging queue, and a person on a preparation team and a
packaging team sees both queues. A queue holds the orders that can be worked now: the preparation
queue the pending preparations, the packaging queue the pending packaging orders whose sale has
nothing left to prepare, the delivery queue the open dispatch orders, with how many ready sales
are on none yet. A cancelled order leaves every queue. Queues are paged and can be filtered and
ordered; see [Fulfillment](../api/fulfillment.md#queues).

## Who does what

- **Admins** change channels, delivery options and which teams handle which type, and register
  sales (app members may register sales too).
- **Teams** work their types as their level allows.
- **Every member** reads channels and delivery options, and sees which queues they have.

The admin site lists all of it and can set up channels, options, sales (with their preparation
orders) and assignments. Every change of state goes through the API, where the rules above are
checked.
