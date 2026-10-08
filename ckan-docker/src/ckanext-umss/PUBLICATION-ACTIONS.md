# The contract of the four publication actions

This file exists because the four actions are consumed by **another session, in another repository**
(`odp`, the portal), and that contract was living only in cross-session messages — a channel that
returns `accepted for delivery`, which is **not** a read receipt. Over one day the same point was
decided four times, each time by whichever message happened to arrive last. The shape is written here
once so both sides read it from the same place.

## Status — read this before trusting the table below

| | |
|---|---|
| The five actions **as delivered** (`unit/a2-governance`, range `63ec806..HEAD`) | implement the governance amendment: `publication_publish` is `sysadmin`-only, `publication_decide` carries the four-eyes and current-capacity re-checks, a lost object is `annulled`, and the returned rows carry `requested_by_name` / `approved_by_name` |
| *(Superseded 2026-10-08)* | The row above describes the **five-action** shape as delivered on 2026-10-07. The author's decision of **2026-10-08** retires `publication_publish` outright and closes the `sysadmin`'s stock route as well: **four actions**, and no caller publishes directly. See the `unit/no-direct-publish` row below. |
| The advisory corrections carried on top (`unit/a2-advisories-v2`) | the branch carries the **eight** advisory findings of A2's first review; **two of them are contract-visible** and are the ones reflected below, both **measured** by the suite: `publication_request_list` resolves every dataset's owner org in **one** query and evaluates the capacity **once per organisation** (behaviour unchanged — it narrows to the same rows — only the helper changed, and the old `_may_see` is gone); and `publication_publish` refuses an **already public** dataset |
| **This file describes** | the contract of `unit/a2-governance`, which is **delivered and natively reviewed**, plus the two contract-visible advisory corrections of `unit/a2-advisories-v2`, plus the `A3` wall (`unit/a3-wall`), **merged to `master`**: its refusal of the stock `package_patch {private: false}` route, any `state` change, a public `package_create`, and `bulk_update_public` — and, since 2026-10-08, the closure of the `sysadmin`'s own route through that wall |
| **`unit/no-direct-publish`** (2026-10-08) | **the author's decision that no caller publishes directly, the `sysadmin` included.** `publication_publish` is **retired from the registry** — a caller of that name gets CKAN's own `400 "Bad request: Action name not known"`, which is the form the author chose over a tombstone that refuses forever; the wall declares `auth_sysadmins_check` on **all three** functions, so it **runs for a `sysadmin`** and refuses the visibility transition for them too, while `state` administration stays the `sysadmin`'s **deliberately** (closing it was never asked for). **Four actions, eight labels.** The label *values* do not change; the **message** is now chosen by fact — a caller the flow authorizes (org `admin` **or** `sysadmin`) reads `Publication flow`, everyone else `Publish denied` — and `Publish denied`'s sentence was corrected, because "only an organization administrator can publish a dataset" became false. *(The native gate on this unit **escalated**: a **CRITICAL** resilience finding states this very consequence — a requester who loses capacity, or who is the only approver, leaves a request nobody can decide, so that dataset never publishes. The maintainer **accepted it on 2026-10-08** as the **declared limitation** of the decision, not as an open defect; the review switch is off for this clone and delivery follows ordinary repository policy.)* |
| **`unit/titles-and-labels`** | **delivered and merged** to `master` in the PR #4 (`02893f7`), natively reviewed (`review-6a40aaa7e5a3b385`, tier high, 4/4 lenses, approved and acknowledged, authority burned; 3 informational findings) and CI green (run `37705879038`): the returned rows also carry `dataset_title` / `organization_title` (resolved in the same single query as the owner organisation, with the names' `None` / `"unknown"` fallbacks), and every refusal below reads `<frozen label>: <free prose>` with the labels declared as constants and pinned by test |
| **`unit/labels-outside-translation`** | the label of every refusal is now composed **outside** `toolkit._(...)`, so the part a consumer matches no longer sits inside a translatable unit; the wall's two sentences stay translated. The label **values** are unchanged (nine at the time; **eight** since 2026-10-08, when the retired action's `Not a sysadmin` went with it). Guarded by a **structural** test, because the defect is invisible to behaviour — see *What is translated, and what is matched* below |
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
it named, the missing-key cost is **closed** (the two name keys and the two title keys now exist); the
`403` cost is **bounded by labels, not closed** — a refusal is still a `403`, but a consumer matches
the frozen label (`<label>: <sentence>`), and the sentence after it can be reworded without breaking
the branch.

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
publish through the **four** actions, **and** the stock `package_patch {private: false}` route is now
refused by the wall — as is any change to a dataset's `state` **below a sysadmin** (`state`
administration stays the `sysadmin`'s, deliberately). So is a public `package_create`, and the
`bulk_update_public` action carries a refusal chain of its own. With the wall closed, the guarantee
holds for the **four** actions **and** for a raw core call by **any** caller; **the `sysadmin`'s own
stock bypass is closed too**: all three wall functions declare `auth_sysadmins_check`, so the wall runs
for them and refuses the visibility transition. *(This supersedes the earlier "what is not yet closed is
the wall" wording and the 2026-10-07 "the sysadmin's own stock bypass remains".)*
The bypasses that remain are named individually under *Named bypasses* below.

## The four actions

| Action | Arguments | Returns | Authorized to |
|---|---|---|---|
| `publication_request_create` | `dataset_id`, `comments?` | the row | a caller who can `update_dataset` in the owning org, on a **private** dataset. Idempotent: answers the existing `pending` row |
| `publication_request_cancel` | `request_id` | the row | the requester, or an org `admin` |
| `publication_request_decide` | `request_id`, `approve` (bool), `comments?` | the row | an `admin` of the owning org, an `admin` of a **parent** org, or a `sysadmin` — **never the requester** |
| `publication_request_list` | `status?` | a list of rows | **any caller may invoke it**: the auth function returns `success: True` unconditionally, and the **action body** narrows the result to what the caller may see (`_datasets_by_id` resolves every dataset's owner org and both titles in one query, the `update_dataset` capacity is evaluated once per organisation, plus the caller's own rows). An anonymous caller is **not** refused before the auth function runs (the same mechanism as rule 7): measured, an anonymous call reached the body and returned a `500` because the dev database has no store table, not a `403` |

## The rules that are not visible in the names

1. **The return is uniform: the row, and nothing else.** `decide {approve: true}` does **not** carry
   the dataset (the retired `publication_publish` also wrote it; it is gone). A consumer that needs the
   dataset's new value
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

   **The queue also carries the dataset's and the organisation's titles**, so the portal can name the
   dataset without an N-per-row `package_show`: `dataset_title` and `organization_title`. They are the
   package's own `title` and a join to the owner organisation's `title`, resolved — **measured** by the
   suite (`test_list_resolves_dataset_and_org_titles_in_one_query_for_the_whole_page`,
   `test_a_single_row_return_resolves_dataset_titles_in_one_query`) — **in the same single query** that
   already resolved the owner organisation for the page:

   ```sql
   SELECT package.id, package.owner_org, package.title, "group".id, "group".title
   FROM package LEFT OUTER JOIN "group" ON "group".id = package.owner_org
   WHERE package.id IN (...)
   ```

   The cost is stated precisely, not as "no query added". On the **list** path the titles add **no**
   query — they ride the organisation query, and a page of three datasets measures exactly one such
   statement. A **single-row** return, by contrast, costs **one extra resolver query per call**: where
   it previously ran one presentation resolver (the names), it now runs two (the names and this one).
   The resolver itself is still one statement per call
   (`test_a_single_row_return_resolves_dataset_titles_in_one_query`). The `"group".id` column is what
   lets the code tell a **missing** group from a group with a blank title.

   The two titles follow the names' fallback shape — **not** the raw id in a field called `..._title`:
   an **unset** source field answers `None` (an unowned dataset has no organisation, so
   `organization_title` is `None`); a source that **is set but does not resolve** (the dataset is gone,
   or its `owner_org` points at a missing group) answers the neutral token `"unknown"`; and a source
   that resolves but is **empty or whitespace-only** answers `None` in both fields — the consumer's
   natural `title ?? fallback` must not receive an empty string that renders as a blank line.
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
   opposite — that `publication_publish` (retired on 2026-10-08, so the transcript below measures the
   build as it stood then) carries no `auth_allow_anonymous_access`, so CKAN would refuse
   an anonymous caller before the auth function ran and they would get a `403` instead of `NotFound`.
   That claim was **wrong**. Re-measured against the running dev stack on **2026-10-07** (the dev
   server bind-mounts this working tree, so it served the labelled message), anonymous, no
   `Authorization` header:

   ```sh
   curl -s -X POST http://localhost:5000/api/3/action/publication_publish \
       -H "Content-Type: application/json" -d '{"dataset_id":"no-such-dataset-xyz"}'
   curl -s -X POST http://localhost:5000/api/3/action/publication_publish \
       -H "Content-Type: application/json" -d '{"dataset_id":"consumo-energetico-por-edificio"}'
   ```

   ```
   no-such-dataset-xyz             -> 404 {"__type":"Not Found Error",
       "message":"Not found: Dataset not found: no-such-dataset-xyz"}
   consumo-energetico-por-edificio -> 403 {"__type":"Authorization Error",
       "message":"Access denied: Not a sysadmin: only a sysadmin may publish a dataset directly"}
   ```

   So the auth function **does** run for an anonymous caller and its `NotFound` path is reachable — the
   property rule 7 rests on, carried today by the create path's own test
   (`test_create_refuses_an_unknown_dataset_and_writes_nothing`). The transcript stays as the record of
   the retired action's build: its refusal on a valid id was that action's own message, not a generic
   "requires an
   authenticated user". Core's early anonymous denial (`ckan/authz.py:235`) only fires when
   `not context.get('auth_user_obj')`; on the API path `ckan/views/api.py:247` sets
   `context['auth_user_obj'] = current_user`, and `login_manager.anonymous_user =
   model.AnonymousUser` (`ckan/config/middleware/flask_app.py:317`) is an `AnonymousUserMixin`
   subclass with no `__bool__`, measured truthy — so that early branch never fires here.
8. **`publication_request_list` is a GET** (`side_effect_free`). It narrows the answer to what the
   caller may see: the stock `update_dataset` capacity on the dataset's org (which cascades down the
   organization hierarchy), plus the caller's own requests.

### Two publish-denial messages, two rules

Publication is refused with one of **three** texts, two of them in the wall:

- the wall in `ckanext.umss.auth` answers the label `Publication flow` (`PUBLISH_VIA_FLOW_MSG`) to a
  caller the flow authorizes — the organization `admin` of the owning organization, **or a `sysadmin`**
  — on the stock `package_update` path it
  chains onto;
- the same wall answers the label `Publish denied` (`PUBLISH_DENIED_MSG`) to a caller core admits
  whose request the approver predicate rejects — in practice an organization `editor`. Callers core
  refuses first (`member`, a cross-organization `editor`, an anonymous caller) never reach the wall on
  that path, and `bulk_update_public` answers every caller that reaches it, the `sysadmin` included,
  because its chain does not call `next_auth`;
- since 2026-10-08 the first bullet above covers the `sysadmin` too: the wall declares
  `auth_sysadmins_check`, so it runs for them and the same `Publication flow` label reaches a
  `sysadmin` who attempts a direct publish. The third bullet this list used to carry — the retired
  action's own `Not a sysadmin` — is **gone with the action**, and with it the name collision that had
  made the labels the way to tell those two denials apart.

The claim this section used to carry — that an organization `admin` may still use the stock
`package_patch {private: false}` route (the wall's door) — is **false as of `A3`**: that route is
refused, with the flow message above. A consumer that reads only one of the two, or assumes they
encode the same rule, will be wrong.

### The refusal labels are interface

The portal tells certain refusals apart by matching the **label** that opens the `message`, because
CKAN gives no machine-readable code for an authorization failure: it always arrives as
`{"__type": "Authorization Error", "message": …}`. The author's decision (2026-10-07) fixes the shape

```
<frozen label>: <free prose>
```

A **literal is a discriminator, not user copy**: the **label** is the interface the portal matches,
and the sentence after the colon is free prose — the sentence the end user reads is the consumer's
own, so improving our wording can no longer break the portal. The portal reads the labels from **one**
constant that points at this file — the pointer is **relayed** from the consuming session, not
reproducible here. That single pointer is what makes this file the one place that can go stale: a
label reworded here, or restated here and no longer matching the code, breaks the consumer's ability
to distinguish the refusal, and nothing in this repository fails. That is why the eight **labels**
below — not their sentences — and the `comments` key of the missing-comment rejection (which is
machine-readable without matching prose) are **pinned by test** (`tests/test_auth.py`,
`tests/test_publication_actions.py`) rather than trusted to review: a reworded sentence does **not**
fail a test, a changed label **does**. Beyond the pins, an **invariant test**
(`test_every_refusal_message_begins_with_a_declared_label`) walks every refusal **declared as a
module-level `*_MSG` constant** in these two modules and fails if any does not open with a declared
label, so a refusal added that way cannot land unlabeled. Its reach is exactly that: it does **not**
see inline strings, handler-local strings, `ValidationError` dict entries, or differently-named
constants — a guard on the declared-message surface, not a proof about every string the code emits.
Each row's label and firing condition is **measured** by the suite; the message column is
illustrative prose.

| label | module | message (the sentence is free) | fires when |
|---|---|---|---|
| `Four eyes` | `logic/auth/publication.py` | `"Four eyes: the approver cannot be the requester of the request they decide"` | `publication_request_decide` is called by the request's own requester who **has** the admin capacity the decision demands — for an org `admin` **and** for a `sysadmin`, which has no exception. The row stays `pending` |
| `Requester capacity` | `logic/auth/publication.py` | `"Requester capacity: the requester can no longer update this dataset, so the request cannot be decided"` | `publication_request_decide` is called while the requester's **current** capacity on the dataset's **current** owner is gone; checked **before** four eyes and before the sysadmin branch, so it has no sysadmin exception either. Reachable by HTTP from a buildable state *(**measured live on 2026-10-08**: the consuming probe built it — a `pending` row whose requester's membership was removed, decided by a **`sysadmin`** — and read `403 Requester capacity`, not `Four eyes`, confirming the order below; the probe then cancelled the request, which is this row's mandated exit)*: a `pending` row whose requester's membership was removed after it was created, or a requester with no user row, or an unresolvable owning organisation; decided by any approver. Mind the order: this check runs **before** four eyes, so an approver reading it gets `Requester capacity` and **not** `Four eyes`. With the direct path retired (2026-10-08) there is **no escape hatch**: no caller, the `sysadmin` included, can decide such a request, so it stays `pending` until the requester cancels it. A consumer's queue must therefore offer `publication_request_cancel` rather than a retry, and a request whose requester is the only approver can never be decided — the **declared limitation** of this capability, not an open defect |
| `Not an approver` | `logic/auth/publication.py` | `"Not an approver: only an organization administrator may decide a publication request"` | the caller is not an org `admin` (owning or parent) and not a `sysadmin`; for a non-sysadmin this branch runs **before** the four-eyes branch |
| `Already public` | `logic/auth/publication.py` | `"Already public: that dataset is already public"` | `publication_request_create` on an already-public dataset — the flip would be a no-op and the caller would only pile up `approved` rows |
| `Cannot request` | `logic/auth/publication.py` | `"Cannot request: only a user who can update this dataset may ask for it to be published"` | `publication_request_create` is called by a caller who cannot `update_dataset` in the dataset's owning organisation |
| `Cannot cancel` | `logic/auth/publication.py` | `"Cannot cancel: only the requester or an organization administrator may cancel this request"` | `publication_request_cancel` is called by neither the requester nor an org `admin` |
| `Publication flow` | `auth.py` (the wall) | `"Publication flow: publication goes through the publication flow, not package_patch"` | a caller the flow authorizes — the org `admin` of the owning organization, **or a `sysadmin`** — attempts the stock `package_update` route (`package_patch`, `package_update`, a public `package_create`, `bulk_update_public`) |
| `Publish denied` | `auth.py` (the wall) | `"Publish denied: only an organization administrator can decide a publication request"` | a caller **core admits** who is not one the flow authorizes — in practice an organization `editor` — attempts the same stock route. On the `package_update` chain only a caller **CKAN admits** reaches the wall at all, so a `member`, a cross-organization `editor` and an anonymous caller keep core's own `403` text and read **no** frozen label; on `bulk_update_public` the wall owns the refusal for **every authenticated caller**, so a `member` reaches this label there too *(**measured live on 2026-10-08**: the consuming portal's probe read `Publish denied` as a `member` through that route — `403`, `Access denied: Publish denied: …`, and **no mutation**, because the refusal happens in the auth before the body)*. The **sentence** was corrected on 2026-10-08 because "only an organization administrator can publish a dataset" became false; the **label** did not change |

The table went from **six rows to nine**, and now stands at **eight**: the retired action's own row
(`Not a sysadmin`) is **gone with the action** (2026-10-08), and with it goes the **name collision** —
two constants called `PUBLISH_DENIED_MSG` in different modules — that had made the labels the only way
to tell the wall's role denial and the action's sysadmin denial apart. Three of the rows above are the
ones the six-row contract lacked: **`Not an approver`** (the decide path's third refusal, which the
earlier contract left unseparated), and **`Cannot request` / `Cannot cancel`** (the create and cancel
refusals, which carried plain sentences with no label at all).

Two notes on scope, so the table is not read as more than it is. On the `package_update` chain the
wall's two labels reach only callers core admits: a `member`, a cross-organization `editor` and an
anonymous caller are refused by core before the chain runs, so they keep core's own `403` text.
`bulk_update_public` is the exception — its chain does not call `next_auth`, so it owns the refusal
and answers every caller that reaches it, the `sysadmin` included, core-admitted or not. Every other
`403` refusal the two modules **declare as a module-level message constant** is a row above; the
invariant test keeps that true for that surface, and the `409` refusals are the separate class below.

*(Measured live on 2026-10-08 by the consuming portal's probe: **all eight labels have an exercised
invoker** — the table above is measured, not inferred — and the core-refusals described in these notes
are measured as such: a `member`, a cross-organization `editor` and an anonymous caller read core's own
text and no frozen label.)*

### What is translated, and what is matched

**The property: what a human reads is translated; what a consumer matches is not.** In the wall the
label is composed **outside** `toolkit._(...)` and the sentence inside it; the six messages of
`logic/auth/publication.py` compose both outside any translator. The eight label **values** are identical
before and after — what this unit moved is the boundary of the translatable unit.

Three facts, measured on the running stack (`babel 2.18.0`, CKAN 2.12.0) while this unit was written, are
what make that boundary the whole point:

1. **`toolkit._` is `ckan.common.ugettext` → `flask_babel.gettext`, and it is eager.** It returns a `str`
   at the moment it is called. In a module-level constant that call happens **once, at import**, under the
   locale in force then — not per request.
2. **This extension ships no message catalogue.** `ckanext/umss/i18n/` holds an empty `.gitignore` and
   nothing else: no `.pot`, no `.po`, no `.mo`, although `setup.cfg` carries the template's
   `[extract_messages]` / `[compile_catalog]` sections pointing at `ckanext/umss/i18n/ckanext-umss.pot`.
3. **This project's extraction does not reach these strings at all.** `setup.cfg` declares
   `keywords = translate isPlural` and `babel.extractors = ckan = ckan.lib.extract:extract_ckan`, and that
   extractor (`ckan/lib/extract.py`) is the **Jinja** one, for templates; for Python the keyword is
   `translate`, not `_`. Under a `_` keyword — which this project does **not** use — babel's extractor
   takes `_("%s: %s" % (LABEL, "…"))` and extracts the template `"%s: %s"` as a message: one that can
   never match at runtime, where the **composed** string is what reaches the translator, and that leaves
   the sentence itself unreachable. Measured, not reasoned.

So the defect this unit closes is **latent and unprovable by behaviour**: the label sat inside the
translator's argument while nothing translates it. It was invisible by inspection — no caller sees a
different string, in any language, today. What it was: one catalogue entry, or one added `_` extraction
keyword, away from silently changing an interface a consumer matches. A guard that asserted the
observable behaviour would have been green with the hole open, which is the same failure shape as the
`_as_bool` mirror that was faithful to a CKAN version that no longer runs.

**The guard is therefore structural and lives where the constants live**
(`test_the_refusal_labels_are_not_translated`): each refusal module is loaded a **second time**, in a
namespace of its own, with `toolkit._` replaced by a translator that marks every string it is handed
(`lambda s: "[t]" + s`), and the invariant the ordinary row checks — *every refusal opens with a declared
label* — is re-run in that marked world. If a label moves back inside `toolkit._(...)`, the mark lands
**in front of** it and the invariant falls. Two companions keep the guard honest: it asserts the mark
**still appears** in the wall's two messages (so a probe whose patch never took effect cannot pass
vacuously, and so the fix is pinned as *label out, sentence stays*), and
`test_the_translation_guardian_can_fail` builds both shapes and shows the guard separates them.

**Deliberately not adopted.** Wrapping the seven action sentences in `toolkit._` as well was considered
and rejected: the portal renders **its own** sentence from the label (this file says so above), so that
prose is support copy rather than user copy, and the asymmetry is now **declared** instead of accidental.
Dropping `_()` from the wall entirely was also rejected: it would remove a declared intent for no gain
today, and that is the author's call, not ours. If a translation is ever wanted for the action sentences,
the change is `_()` **around the sentence only** — the guard above already accepts that shape and rejects
the other one.

### Two classes of refusal: `403` carries a label, `409` carries a key

Every refusal this contract documents arrives as one of exactly two API shapes, and a consumer must
branch on a different part of each.

- A **`403` authorization refusal** is `{"__type": "Authorization Error", "message": "Access
  denied: <Label>: …"}`. The label is the interface (the section above); the sentence after the colon
  is free prose. There is **no** field key, so the label is the only discriminator.
- A **`409` validation refusal** is `{"__type": "Validation Error", "<field>": [ … ]}` with the
  payload **keyed by field**, **no** `Access denied:` wrapper and **no** `message` key: CKAN's
  `ckan/views/api.py` maps `ValidationError` to `409` and copies `error_dict` verbatim. The **key** is
  the interface. Measured and executed on 2026-10-07 (`test_publication_actions.py` probe), there are
  **three** keys:

  | key | trigger | observed payload |
  |---|---|---|
  | `comments` | `publication_request_decide {approve: false}` with no non-blank `comments` | `{"comments": ["Missing value: a rejection must carry a comment"]}` |
  | `request_id` | the request is not `pending`: `decide` → `"That request is no longer pending"` (decided or cancelled by someone else — the realistic queue race); `cancel` → `"Only a pending request can be cancelled"`; and a missing or blank `request_id` → `"Missing value"` | `{"request_id": ["That request is no longer pending"]}` |
  | `approve` | `approve` is absent or not a boolean | `{"approve": ["Missing value: true or false"]}` |

A consumer must read the **key** on a `409` and the **label** on a `403`. Presenting the `request_id`
case as *"the decision could not be registered"* sends the user to retry something that can never
succeed; the truth is *"this request was already decided"*, and the form should close and the queue
refresh. The key is the part a consumer branches on; the sentence inside it is presentation and may be
reworded.

**The governance rule: the trigger to design a machine-readable token is the second consumer
branch, not the second message.** Counted as labels there are already **eight**; counted as places
where the consumer must **change behaviour** there is exactly **one** — four eyes, where the refusal
means "you cannot decide your own request" and the portal must route elsewhere rather than merely
display the text. One case is an exception, two cases are a shape; the day a second branch appears,
the token is designed **before** the branch is wired, not after. Until then the eight labels stay
pinned text, and this file stays the single point the consumer's constant points at.

**The precedence of the three decide-path labels is measured, not asserted.** The decide path alone
carries `Requester capacity`, `Not an approver` and `Four eyes`, all `403`. They are ordered: the
requester's **current capacity** is checked first, then (for a non-sysadmin) the approver capacity,
and only then four eyes. `Four eyes` therefore appears only for a requester who **has** the admin
capacity the decision demands; a requester who lost it answers `Requester capacity`
(`test_the_decide_path_checks_capacity_before_four_eyes`), and a non-approver requester answers
`Not an approver` (`test_a_non_approver_requester_gets_not_an_approver_not_four_eyes`).

**Standing note on the price of that future token.** The portal already reads CKAN's `__type` and
discriminates with it elsewhere — **relayed**: it is the consuming repository, not this one — so an
own error type would cost less on the consumer side than it looks, because part of the reader for it
already exists. That changes the **price** of the future decision; it does not make the decision
today. The contract above stands: eight pinned labels, one single pointer, and the second branch as
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
  reused. **Observed in the wild, 2026-10-08** *(relayed from the consuming portal's probe)*: five probe
  runs left **48** rows in the store whose datasets no longer existed — **25 `annulled`** (those went
  through `package_delete`, which **does** fire the hook) and **19 `approved` + 3 `cancelled` + 1
  `pending`** (those went through `dataset_purge`, which does not). A probe that trusts the hook leaves
  residue in the store **while its catalog reports zero leftovers**: the two counts look at different
  tables. The fix on that side is to delete the store rows before purging, not to rely on the hook.
- **`bulk_update_delete` does not fire it either.** It soft-deletes through
  `_bulk_update_dataset(..., {'state': 'deleted'})`, which loops `package_patch`; the wall now refuses
  an organization `admin`'s `state` change, so that admin's delete is refused before it removes the
  dataset and no `pending` row survives it. The bypass that remains is the **`sysadmin`'s own**
  `bulk_update_delete`: since 2026-10-08 the wall *does* declare `auth_sysadmins_check`, but a `state`
  change is deliberately **preserved** for a `sysadmin` (it is not publishing), so a sysadmin delete
  still leaves the row behind, as for `dataset_purge`. The
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
- **The one sanctioned door bypasses the wall by design.** `publication_request_decide {approve: true}`
  writes through `package_patch` with `ignore_auth`, which `authz.is_authorized` short-circuits before
  consulting any registered auth function (`ckan/authz.py:210-213`). It is the only path meant to
  publish, and since 2026-10-08 it is the **only** one at all: `publication_publish` was the second and
  is retired. The wall refusing this door would make the publication flow impossible. Design, not a gap.

## The four governance deltas this unit carries

From `odp` `41de6c2`:

1. **As delivered:** `publication_publish` is `sysadmin`-only; an org `admin` has **no direct publish
   path through the five actions**. The stock `package_patch {private: false}` route is no longer open
   to an organization `admin` either: the wall (`A3`) refuses it, as it does any `state` change.
   **Superseded 2026-10-08:** the action is retired and the `sysadmin`'s stock route is closed too —
   **no caller publishes directly**, and `state` administration (not a publication) stays the
   `sysadmin`'s, deliberately.
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
