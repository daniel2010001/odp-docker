# The contract of the five publication actions

This file exists because the five actions are consumed by **another session, in another repository**
(`odp`, the portal), and that contract was living only in cross-session messages — a channel that
returns `accepted for delivery`, which is **not** a read receipt. Over one day the same point was
decided four times, each time by whichever message happened to arrive last. The shape is written here
once so both sides read it from the same place.

## Status — read this before trusting the table below

| | |
|---|---|
| The five actions **as delivered** (`unit/a2-governance`, range `63ec806..HEAD`) | implement the governance amendment: `publication_publish` is `sysadmin`-only, `publication_decide` carries the four-eyes and current-capacity re-checks, a lost object is `annulled`, and the returned rows carry `requested_by_name` / `approved_by_name` |
| The advisory corrections carried on top (`unit/a2-advisories-v2`) | the branch carries the **eight** advisory findings of A2's first review; **two of them are contract-visible** and are the ones reflected below, both **measured** by the suite: `publication_request_list` resolves every dataset's owner org in **one** query and evaluates the capacity **once per organisation** (behaviour unchanged — it narrows to the same rows — only the helper changed, and the old `_may_see` is gone); and `publication_publish` refuses an **already public** dataset |
| **This file describes** | the contract of `unit/a2-governance`, which is **delivered and natively reviewed**, plus the two contract-visible advisory corrections of `unit/a2-advisories-v2`, plus the `A3` wall (`unit/a3-wall`): its refusal of the stock `package_patch {private: false}` route, any `state` change, a public `package_create`, and `bulk_update_public`. The wall's change is **on the branch**, not yet reviewed or delivered |
| The review of record (**relayed**) | lineage `review-4b6ecc112fa966fa`, tier **high**, **4/4 lenses**, **approved and acknowledged**; the review authority is burned; the **9 findings are informational**, none blocking, none reopening the lineage, and they are declared as later work at the end of this file. This metadata is **relayed** from the provider's review envelope and from the state file read before acknowledgement — it is **not reproducible from the repository now** |
| The governance amendment | `odp` commit `41de6c2`, `design.md:194,207` and `spec.md:284,586` |
| The unit's state and what it must carry | `HANDOFF-2026-10-07.md` (tracked, repository root) |

**Provenance of the review metadata above.** The tier, lens count, approval/acknowledgement and
authority-burned facts are **relayed** from the provider's review envelope and from the state file
read before acknowledgement. They are **not verified in this repository**: acknowledgement consumed
that state and left only a terminal-consumption stub (`terminal-consumption/v1/<hash>.json`) that
records the lineage, the repository and target hashes, and no findings. Treat them as a faithful relay,
not as something a later reader can re-derive from disk.

**The table below can be wired against.** It is the delivered shape, not a proposal: the earlier
warning — "do not wire a consumer until the unit lands" — no longer applies. Of the two adoption costs
it named, the missing-key cost is **closed** (the two name keys now exist); the `403` cost is
**documented, not closed** — the four-eyes refusal is still a `403`, and a consumer must still read the
message to tell it apart from other `403`s.

**A `403` from a stock core call can now be ours.** A `403` from `package_patch`,
`package_update` or `bulk_update_public` is no longer necessarily CKAN's own: with `A3`, the wall
answers `PUBLISH_VIA_FLOW_MSG` for the approver and `PUBLISH_DENIED_MSG` for a caller **core admits**
whose request the approver predicate rejects — in practice an organization `editor`. On the
`package_update` chain, callers core refuses before the chain runs (`member`, a cross-organization
`editor`, an anonymous caller) never see the wall's message and keep core's own `403`.
`bulk_update_public` is the exception: its chain does not call `next_auth`, so it owns the refusal and
answers every authenticated non-sysadmin that reaches it. On the API path it answers an anonymous
caller too — CKAN's early anonymous denial fires only without a truthy `auth_user_obj` (rule 7);
measured, an anonymous `POST` to the action returned the wall's `PUBLISH_DENIED_MSG`, not an
"authenticated user" refusal. A consumer must read the message on a stock-core `403` too, not assume
the text is core's.

**The wall has landed (`A3`).** An organization `admin` can no longer approve their own request or
publish through the five actions, **and** the stock `package_patch {private: false}` route is now
refused by the wall, as is any change to a dataset's `state`; so is a public `package_create`, and the
`bulk_update_public` action carries a refusal chain of its own. With the wall closed, the guarantee
holds for the five actions **and** for a raw core call by any caller below a sysadmin; the sysadmin's
own stock bypass remains. *(This supersedes the earlier "what is not yet closed is the wall" wording.)*
The bypasses that remain are named individually under *Named bypasses* below.

## The five actions

| Action | Arguments | Returns | Authorized to |
|---|---|---|---|
| `publication_request_create` | `dataset_id`, `comments?` | the row | a caller who can `update_dataset` in the owning org, on a **private** dataset. Idempotent: answers the existing `pending` row |
| `publication_request_cancel` | `request_id` | the row | the requester, or an org `admin` |
| `publication_request_decide` | `request_id`, `approve` (bool), `comments?` | the row | an `admin` of the owning org, an `admin` of a **parent** org, or a `sysadmin` — **never the requester** |
| `publication_publish` | `dataset_id`, `comments?` | the row | **`sysadmin` only**, and only on a **private** dataset. An already public dataset is refused (`403`, `"That dataset is already public"`) instead of accumulating a second `approved` row — the same guard `publication_request_create` applies. Capacity is checked **before** state, so a non-sysadmin gets the sysadmin denial whatever the dataset's visibility: its `403` text does not change with the dataset (measured: `test_publish_refuses_a_dataset_that_is_already_public` for the sysadmin, `test_a_non_sysadmin_publishing_an_already_public_dataset_gets_the_sysadmin_denial` for the non-sysadmin) |
| `publication_request_list` | `status?` | a list of rows | **any caller may invoke it**: the auth function returns `success: True` unconditionally, and the **action body** narrows the result to what the caller may see (`_orgs_by_dataset` resolves every dataset's owner org in one query, the `update_dataset` capacity is evaluated once per organisation, plus the caller's own rows). An anonymous caller is **not** refused before the auth function runs (the same mechanism as rule 7): measured, an anonymous call reached the body and returned a `500` because the dev database has no store table, not a `403` |

## The rules that are not visible in the names

1. **The return is uniform: the row, and nothing else.** `publication_publish` and
   `decide {approve: true}` do **not** carry the dataset. A consumer that needs the dataset's new value
   **re-reads it** — that verifies the effect at the source instead of trusting the response of the
   action that wrote it. Decided in favour of the uniform shape over an additive `dataset` key, and it
   is final: **the shape does not change after a consumer is wired.**
2. **`requested_by` and `approved_by` are user _ids_**, CKAN's convention (`package_show` answers
   `creator_user_id`). Comparing them against a username is a silently dead check: it has already
   happened once, on the consuming side, and left a four-eyes rule implemented, green and inert.
3. **Rows carry presentation names** so the queue does not resolve N users per page:
   `requested_by_name` and `approved_by_name` (CKAN usernames), resolved **in one batched query per
   call**. The two empties are distinct: an **unset** id column answers `None`, and an id that **is set
   but does not resolve** answers the neutral token `"unknown"` — **never the raw id** (a field called
   `..._name` must never contain an id). `approved_by_name` is only meaningful on `approved` and
   `rejected`; on `cancelled` the requester withdrew and on `annulled` there was no decision.
4. **`comments` is required when rejecting**, optional when approving.
5. **The refusal of an approver's own request is distinguishable** (`403` with its own message, the row
   stays `pending`), never a silent no-op.
6. **`annulled` is not `cancelled`:** a pending request is annulled when its object disappears (the
   dataset is deleted, or it became public by another route), and by a direct `publish`. `cancelled` is
   the requester withdrawing. The `motive` column records the trigger as a **cross-repository token**
   the portal matches on — `dataset_deleted` and `published_by_another_path` — and both literals are
   pinned by a test (`tests/test_publication_actions.py`).
7. **An unresolvable `request_id` or `dataset_id` answers `NotFound`, not `403`.** A `403` would report
   a missing thing as a missing capacity. Deriving from that: the auth functions answer `success` for an
   unresolvable id on purpose, and the **actions** are where existence is checked — which is what stops
   an orphan row for a dataset that does not exist.

   **Measured, and anonymous callers are inside the rule.** An earlier draft of this file claimed the
   opposite — that `publication_publish` carries no `auth_allow_anonymous_access`, so CKAN would refuse
   an anonymous caller before the auth function ran and they would get a `403` instead of `NotFound`.
   That claim was **wrong**. Measured against the running dev API:

   ```
   anonymous + unknown dataset_id -> 404 {"__type":"Not Found Error",
       "message":"Not found: Dataset not found: no-such-dataset-xyz"}
   anonymous + valid dataset_id   -> 403 {"__type":"Authorization Error",
       "message":"Access denied: Only a sysadmin may publish a dataset directly"}
   ```

   So the auth function **does** run for an anonymous caller and its `NotFound` path is reachable; the
   refusal on a valid id is the action's own message (`PUBLISH_DENIED_MSG`), not a generic "requires an
   authenticated user". Core's early anonymous denial (`ckan/authz.py:235`) only fires when
   `not context.get('auth_user_obj')`; on the API path `ckan/views/api.py:247` sets
   `context['auth_user_obj'] = current_user`, and `login_manager.anonymous_user =
   model.AnonymousUser` (`ckan/config/middleware/flask_app.py:317`) is an `AnonymousUserMixin`
   subclass with no `__bool__`, measured truthy — so that early branch never fires here.
8. **`publication_request_list` is a GET** (`side_effect_free`). It narrows the answer to what the
   caller may see: the stock `update_dataset` capacity on the dataset's org (which cascades down the
   organization hierarchy), plus the caller's own requests.

### Three publish-denial messages, three rules

Publication is refused with one of **three** texts, two of them in the wall:

- the wall in `ckanext.umss.auth` answers `PUBLISH_VIA_FLOW_MSG` (`"Publication goes through the
  publication flow, not package_patch"`) to an **approver** — the organization `admin` the flow
  authorizes — on the stock `package_update` path it chains onto;
- the same wall answers `PUBLISH_DENIED_MSG` (`"Only an organization administrator can publish a
  dataset"`) to a caller core admits whose request the approver predicate rejects — in practice an
  organization `editor`. Callers core refuses first (`member`, a cross-organization `editor`, an
  anonymous caller) never reach the wall on that path, and `bulk_update_public` answers every
  authenticated non-sysadmin that reaches it, because its chain does not call `next_auth`;
- the `publication_publish` action answers `"Only a sysadmin may publish a dataset directly"`
  (`PUBLISH_DENIED_MSG` in its own module; the two modules name a constant the same way).

The claim this section used to carry — that an organization `admin` may still use the stock
`package_patch {private: false}` route (the wall's door) — is **false as of `A3`**: that route is
refused, with the flow message above. A consumer that reads only one of the three, or assumes they
encode the same rule, will be wrong.

### The refusal literals are interface

The portal tells certain refusals apart by matching the `message` text, because CKAN gives no
machine-readable code for an authorization failure: it always arrives as
`{"__type": "Authorization Error", "message": …}`. The portal reads those texts from **one**
constant that points at this file — the pointer is **relayed** from the consuming session, not
reproducible here. That single pointer is what makes this file the one place that can go stale: a
literal reworded here, or restated here and no longer matching the code, breaks the consumer's
ability to distinguish the refusal, and nothing in this repository fails. That is why the six refusal
literals below — and the `comments` key of the missing-comment rejection, which is machine-readable
without matching prose — are **pinned by test** (`tests/test_auth.py`,
`tests/test_publication_actions.py`) rather than trusted to review. Each row's value and firing
condition is **measured** by the suite.

| literal | module | fires when |
|---|---|---|
| `DECIDE_FOUR_EYES_MSG` (`"Four eyes: the approver cannot be the requester of the request they decide"`) | `logic/auth/publication.py` | `publication_request_decide` is called by the request's own requester — for an org `admin` **and** for a `sysadmin`, which has no exception. The row stays `pending` |
| `DECIDE_REQUESTER_CAPACITY_MSG` (`"The requester no longer has permission to update this dataset, so the request cannot be decided"`) | `logic/auth/publication.py` | the requester's **current** capacity on the dataset's **current** owner is gone; checked before the sysadmin branch, so it has no sysadmin exception either |
| `ALREADY_PUBLIC_MSG` (`"That dataset is already public"`) | `logic/auth/publication.py` | `publication_request_create` on an already-public dataset, and the sysadmin's `publication_publish` on one — the flip would be a no-op and the caller would only pile up `approved` rows |
| `PUBLISH_VIA_FLOW_MSG` (`"Publication goes through the publication flow, not package_patch"`) | `auth.py` (the wall) | an **approver** — the org `admin` the flow authorizes — attempts the stock `package_update` route (`package_patch`, `package_update`, a public `package_create`, `bulk_update_public`). The message names the flow because the caller already holds the role |
| `PUBLISH_DENIED_MSG` (`"Only an organization administrator can publish a dataset"`) | `auth.py` (the wall) | a caller **core admits** whose request the approver predicate rejects — in practice an organization `editor` — attempts the same stock route |
| `PUBLISH_DENIED_MSG` (`"Only a sysadmin may publish a dataset directly"`) | `logic/auth/publication.py` | `publication_publish` is called by **any non-sysadmin**; the capacity predicate runs before the state one, so the same denial covers a non-sysadmin on an already-public dataset too (measured: `test_a_non_sysadmin_publishing_an_already_public_dataset_gets_the_sysadmin_denial`) |

Two notes on scope, so the table is not read as more than it is. The **name `PUBLISH_DENIED_MSG`
is not unique**: the two rows that carry it are different constants in different modules — the wall's
in `auth.py` and the `publication_publish` action's in `logic/auth/publication.py` — and a consumer
choosing between them must read the module as well as the name. Both are pinned, one per module. And
on the `package_update` chain the wall's two literals reach only callers core admits: a `member`, a
cross-organization `editor` and an anonymous caller are refused by core before the chain runs, so
they keep core's own `403` text. `bulk_update_public` is the exception — its chain does not call
`next_auth`, so it owns the refusal and answers every authenticated non-sysadmin that reaches it,
core-admitted or not.

**One refusal in this contract is not prose, and is already machine-readable.** A rejection with no
comment is not a `403` at all: it is a `ValidationError` whose error dict is keyed on `comments` —
`{"comments": ["Missing value: a rejection must carry a comment"]}` — so a consumer should present
it as *"the motive is missing"* and keep the form open, not as *"the decision could not be
registered"*. The two are different failures, and CKAN already distinguishes them without matching
text. The **key** is pinned by test; the sentence inside it is not, because the key is the part a
consumer branches on and the sentence is presentation.

**The governance rule: the trigger to design a machine-readable token is the second consumer
branch, not the second message.** Counted as messages there are already **six**; counted as places
where the consumer must **change behaviour** there is exactly **one** — four eyes, where the refusal
means "you cannot decide your own request" and the portal must route elsewhere rather than merely
display the text. One case is an exception, two cases are a shape; the day a second branch appears,
the token is designed **before** the branch is wired, not after. Until then the six values stay
pinned text, and this file stays the single point the consumer's constant points at.

**Standing note on the price of that future token.** The portal already reads CKAN's `__type` and
discriminates with it elsewhere — **relayed**: it is the consuming repository, not this one — so an
own error type would cost less on the consumer side than it looks, because part of the reader for it
already exists. That changes the **price** of the future decision; it does not make the decision
today. The contract above stands: six pinned literals, one single pointer, and the second branch as
the trigger.

### Named bypasses

`A3.4`'s inventory — a literal transcript of every API-reachable writer of `private`/`state` and the
coverage each gets — is attached to the `unit/a3-wall` pull request, not duplicated here. What follows
are the paths this contract deliberately leaves open, split into the annulment bypasses and the wall's
own exceptions; each is named so none is a future surprise, and each is either **measured** on the
running CKAN 2.12.0 (`/srv/app/src/ckan`, `0058b2eb…`) or marked as **relayed** from the inventory.

**Annulment bypasses** (the dataset disappears or is re-added without the hook that annuls):

- **`dataset_purge` does not fire the deletion hook.** `after_dataset_delete` is invoked only from
  `package_delete` (`ckan/logic/action/delete.py`), and `dataset_purge` purges directly: it hard-deletes
  the row (`pkg.purge()`) and writes neither `private` nor `state`. It is `sysadmin`-only
  (`ckan/logic/auth/delete.py`), so the blast radius is narrow: a purge with a `pending` request leaves
  the row behind, nothing annuls it, and the single-`pending` index only bites if the dataset id is
  reused.
- **`bulk_update_delete` does not fire it either.** It soft-deletes through
  `_bulk_update_dataset(..., {'state': 'deleted'})`, which loops `package_patch`; the wall now refuses
  an organization `admin`'s `state` change, so that admin's delete is refused before it removes the
  dataset and no `pending` row survives it. The bypass that remains is the **`sysadmin`'s own**
  `bulk_update_delete`, which the wall leaves to the stock bypass (none of its chains sets
  `auth_sysadmins_check`): a sysadmin delete still leaves the row behind, as for `dataset_purge`. The
  entry stays named rather than deleted because that sysadmin path is real.
- **A soft-deleted user still resolves.** `model.User.get` filters on `name` or `id`, not on `state`,
  so a soft-deleted user row is still returned. The decision's fail-closed path therefore triggers on
  a requester id with **no user row at all**, or on the requester's membership being gone — not on the
  user row being soft-deleted. The residual hole — a soft-deleted user who somehow keeps an active
  membership — is narrow: `user_delete` also deletes the memberships, so it needs hand-built DB state,
  a migration, or re-adding the deleted user with `member_create`.

**The wall's intended exceptions and its one config-gated gap** (API-reachable writers of
`private`/`state` the wall does not refuse):

- **`package_delete` is an intended exception, not a hole.** Its auth delegates to `package_update`
  (`ckan/logic/auth/delete.py`), but the request carries only `id` — no `private` and no `state` key —
  so the wall returns the core result, and the `state='deleted'` write happens at the model level, in
  `StatefulObjectMixin.delete` (`ckan/model/core.py`). `A3.3` requires `package_delete` to keep
  working, so the wall must not refuse it.
- **`bulk_update_private` is intended.** It narrows visibility rather than publishing, and the
  `package_update` chain permits it: the inner `package_patch` loop carries `private: True`, which is
  never a publication.
- **`package_create` without `owner_org` is the gap the inventory exists to name.** The wall returns
  the core result whenever `owner_org` is absent, so a **public unowned create** would not be refused.
  It is unreachable today **only** because `ckan.auth.create_unowned_dataset = false` in `ckan.ini`; if
  that config is enabled it becomes a real publication path. This is the one entry here that is a
  genuine, reachable gap rather than an intended exception.
- **The two sanctioned doors bypass the wall by design.** `publication_request_decide {approve: true}`
  and `publication_publish` both write through `package_patch` with `ignore_auth`, which
  `authz.is_authorized` short-circuits before consulting any registered auth function
  (`ckan/authz.py:210-213`). They are the only paths meant to publish; the wall refusing them would
  make the publication flow impossible. Design, not a gap.

## The four governance deltas this unit carries

From `odp` `41de6c2`:

1. `publication_publish` is `sysadmin`-only; an org `admin` has **no direct publish path through the
   five actions**. The stock `package_patch {private: false}` route is no longer open to an
   organization `admin` either: the wall (`A3`) refuses it, as it does any `state` change.
2. `decide` is four-eyes: an org `admin` (owning or parent) or a `sysadmin`, **never the requester**.
3. `comments` is required when rejecting.
4. The decision **re-checks the current state** — the dataset's current owner and the requester's
   current capacity — and a pending request whose object is gone is **annulled**.

## Declared later work: the 9 informational findings of `review-4b6ecc112fa966fa`

The native gate approved the accumulated candidate at lineage `review-4b6ecc112fa966fa` (tier
**high**, 4/4 lenses) and left **9 informational findings**: none blocking, none reopening the
lineage. For **this** lineage the state file carried the findings' **ids, lenses, severities and
locations** but not their prose, and that file is now gone: after acknowledgement the only durable
artifact is a terminal-consumption stub (`terminal-consumption/v1/<hash>.json`) that records the
lineage, the repository and target hashes, and no findings. So this list records the **ids and
locations**, not the prose, and it is not a
substitute for the original review. That absence is **specific to this lineage**: the `state` object
does hold findings with their prose for other lineages — `review-df2b5906cb9cc5ea`, measured on disk,
stores nine findings each with a `claim` — so the loss is not a property of the storage format.

**The `location` column holds review-time line numbers.** They were accurate when the gate ran, and
the files have moved since — the `get_actions` docstring alone added lines to `plugin.py`. A reader who
greps a range will land near but not exactly on the code. They are deliberately **not recomputed**
here: recomputing invites the same staleness after the next edit.

| id | lens | severity | location |
|---|---|---|---|
| `R1-ANNUL-COVERAGE` | risk | WARNING | `ckanext/umss/plugin.py:84-95` |
| `R1-DECIDE-PREAUTH-PROBE` | risk | SUGGESTION | `ckanext/umss/logic/auth/publication.py:183-190` |
| `R2-001` | readability | SUGGESTION | `ckanext/umss/logic/auth/publication.py:57` |
| `R2-002` | readability | SUGGESTION | `ckanext/umss/plugin.py:113` |
| `R3-DELETE-HOOK-GAP` | reliability | WARNING | `ckanext/umss/plugin.py:82-95` |
| `R3-LOCAL-TIMESTAMP` | reliability | SUGGESTION | `ckanext/umss/plugin.py:126-127` |
| `R3-REJECT-CAPACITY-UNTESTED` | reliability | WARNING | `ckanext/umss/logic/auth/publication.py:186-187` |
| `R4-DELETE-GAP` | resilience | WARNING | `ckanext/umss/plugin.py:79-95` |
| `R4-REJECT-STUCK` | resilience | WARNING | `ckanext/umss/logic/auth/publication.py:186` |

**Three lenses independently found the same finding** — the delete-hook coverage:
`R1-ANNUL-COVERAGE`, `R3-DELETE-HOOK-GAP` and `R4-DELETE-GAP` all point at `plugin.py:79-95`. That
convergence is why *Named bypasses* above names the `dataset_purge` and `bulk_update_delete` gaps
explicitly rather than leaving them to be rediscovered.

**`R3-LOCAL-TIMESTAMP` measured:** the finding is **not** two modules disagreeing about "now". The
action module's `_now()` **is** `datetime.datetime.now()` (`ckanext/umss/logic/action/publication.py`),
the same naive local time the plugin hook uses, so the finding is the naive local timestamp — plus a
duplicated helper — not a divergence between modules.
