# The contract of the five publication actions

This file exists because the five actions are consumed by **another session, in another repository**
(`odp`, the portal), and that contract was living only in cross-session messages — a channel that
returns `accepted for delivery`, which is **not** a read receipt. Over one day the same point was
decided four times, each time by whichever message happened to arrive last. The shape is written here
once so both sides read it from the same place.

## Status — read this before trusting the table below

| | |
|---|---|
| The five actions **as delivered** (`1a2d6c2`) | implement **D4 before the governance amendment**: `publication_publish` authorizes an org `admin`, and `publication_decide` has **no** four-eyes check |
| **This file describes** | the contract of `unit/a2-governance`, which is **in flight** |
| The governance amendment | `odp` commit `41de6c2`, `design.md:194,207` and `spec.md:284,586` |
| The unit's state and what it must carry | `HANDOFF-2026-10-07.md` (tracked, repository root) |

So: **do not wire a consumer against the table below until that unit lands.** Both sides have already
paid for adopting a contract ahead of the code — once as a `403` read as a failure, once as a missing
key read as a failure.

**The gap is live, not theoretical:** in the dev stack today, a token with the `admin` capacity of the
owning organization approves its own request and publishes directly, because the delivered code predates
the amendment. Nothing in the portal prevents it: the guarantee lives in CKAN's authorization layer, so
until `unit/a2-governance` lands the rule is enforced nowhere but in the portal's UI.

## The five actions

| Action | Arguments | Returns | Authorized to |
|---|---|---|---|
| `publication_request_create` | `dataset_id`, `comments?` | the row | a caller who can `update_dataset` in the owning org, on a **private** dataset. Idempotent: answers the existing `pending` row |
| `publication_request_cancel` | `request_id` | the row | the requester, or an org `admin` |
| `publication_request_decide` | `request_id`, `approve` (bool), `comments?` | the row | an `admin` of the owning org, an `admin` of a **parent** org, or a `sysadmin` — **never the requester** |
| `publication_publish` | `dataset_id`, `comments?` | the row | **`sysadmin` only** |
| `publication_request_list` | `status?` | a list of rows | anyone who administers or edits in the org |

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
   call**. Neutral fallback, **never the raw id**. `approved_by_name` is only meaningful on `approved`
   and `rejected`; on `cancelled` the requester withdrew and on `annulled` there was no decision.
4. **`comments` is required when rejecting**, optional when approving.
5. **The refusal of an approver's own request is distinguishable** (`403` with its own message, the row
   stays `pending`), never a silent no-op.
6. **`annulled` is not `cancelled`:** a pending request is annulled when its object disappears (the
   dataset is deleted, or it became public by another route), and by a direct `publish`. `cancelled` is
   the requester withdrawing.
7. **An unresolvable `request_id` or `dataset_id` answers `NotFound`, not `403`.** A `403` would report
   a missing thing as a missing capacity. Deriving from that: the auth functions answer `success` for an
   unresolvable id on purpose, and the **actions** are where existence is checked — which is what stops
   an orphan row for a dataset that does not exist.
8. **`publication_request_list` is a GET** (`side_effect_free`). It narrows the answer to what the
   caller may see: the stock `update_dataset` capacity on the dataset's org (which cascades down the
   organization hierarchy), plus the caller's own requests.

## The four governance deltas this unit carries

From `odp` `41de6c2`:

1. `publication_publish` is `sysadmin`-only; an org `admin` has **no** direct publish path.
2. `decide` is four-eyes: an org `admin` (owning or parent) or a `sysadmin`, **never the requester**.
3. `comments` is required when rejecting.
4. The decision **re-checks the current state** — the dataset's current owner and the requester's
   current capacity — and a pending request whose object is gone is **annulled**.
