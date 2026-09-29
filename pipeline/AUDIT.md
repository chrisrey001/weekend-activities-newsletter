# Kiwi's Weekend Guide — Send-Path Hardening Audit

**Auditor:** test-and-audit subagent · **Date:** 2026-09-17 (after production run #1)
**Scope:** `~/workspace/kiwis-corner/pipeline/` — the durable pipeline built by
two sibling agents in response to the 9/17 incident. No real email was sent,
no Gmail inbox was read, and no cron was created/changed during this audit
(all tests use mocked Gmail adapters; scheduling belongs to the coordinator).

## Incident recap (from PLAN.md)

Production run #1 (Thu 2026-09-17, ~08:00 MDT) completed research/render (72
verified events) but the Gmail send parked on an unanswered connector
approval from ~08:50 MDT while the run log falsely recorded "sent once" with
no message ID. The watchdog caught it at 09:30; the legacy sender's parallel send covered the household. Root cause: no verified-send gate. The real send completed at 10:21
after the owner approved.

---

## 1. Durable pipeline built — what was missing, what now exists

**What was missing:** the 9/17 run had no single parameterized pipeline at
all — the send was ad hoc and the run log was written by the worker, so a
false "sent" claim could land with zero evidence.

**What now exists** (all under `~/workspace/kiwis-corner/pipeline/`):

| File | Role |
|---|---|
| `run.py` | Single entrypoint: weekend dates → verify → enrich → render → pre-flight → send → run-log. No date-suffixed anything. |
| `send_hardened.py` | Verified send gate. `SendResult` dataclass (`status`, `message_id`, `notes`); `send_newsletter(*, html_body, subject, recipient, run_id, dry_run, gmail)`; `CliGmailAdapter` subprocess wrapper. |
| `calendar_context.py` | Pure 3-tier calendar classifier + `overlaps()` helper. |
| `weather_hook.py` | Deterministic weather facts (rain-risk days ≥ 50% precip). |
| `template.py` | Inline-styles HTML email template (promoted from the 9/17 render). |
| `approval_watch.md` | Approval-wait alert design + cron specs (8:45 watch, 9/24 run + watchdog). |
| `approval_watch_logic.py` | **New in this audit.** Pure decision function transcribing the approval-watch cron body's three steps (`decide(sent_today, pending_kiwis_approval) → "all_clear" \| "approval_alert" \| "needs_attention"`). |
| `RUNBOOK.md` | Operator instructions; §2 gate table documents the new failed-send halt. |
| `tests/` | 94 tests (71 pre-existing + 23 new in this audit), all passing. |

**Gap found & fixed in this audit:** the old pipeline exited 0 even on a
failed send (a silent success — same bug family as the incident). `run.py`
`main()` now writes the run log first, then raises `PipelineHalt`
(non-zero exit) when `SendResult.status == "failed"`. RUNBOOK.md §2 documents
this.

---

## 2. Sent-gate correctness — the false "sent once" bug

**The bug:** nothing stopped the log from saying "sent" without a Gmail
message ID.

**The two-evidence rule** (`send_hardened.py::send_newsletter`):
1. `send()` must return a non-empty message ID (Step 3 — `None`/empty/
   unparseable → `"failed"`; the module never invents an ID).
2. A post-send Sent search must return that exact ID, dated today
   (America/Denver), addressed to the recipient (Step 4).

`"sent"` is returned from **exactly one code path** (the Step-4 success) —
pinned by `test_single_sent_return_in_source`, which regex-counts
`SendResult("sent"` occurrences in the source.

**Idempotency:** Step 1 searches today's Sent *before* any send attempt; an
exact subject + recipient + today match returns `"already_sent"` with zero
send attempts. Search failures abort fail-closed (`"failed"`, never "send
blind").

**Gap found & fixed in this audit:** `build_run_log()` in `run.py` raises
rather than write a log containing the standalone word "sent" unless status
is `"sent"` (the incident rule). But the send module's own failure notes
said e.g. *"post-send **Sent** confirmation missing"*, *"check **Sent**
before any retry"* — in production, a failed run would have raised at
log-build time and **no run log would have been written at all**, losing the
diagnostics. All non-`"sent"` notes were rephrased ("delivered mail",
"mailbox", "post-send verification missing…") so every notes string the
module can produce is contract-clean (verified by an exhaustive notes sweep:
0 violations). Three existing assertions were updated to the new wording
(product fix, not test softening).

---

## 3. Approval-wait alert — what cron, when, what it does

Spec lives in `pipeline/approval_watch.md` (coordinator creates the crons;
none exist yet):

- **`kiwis-corner-send-approval-watch`** — Thursdays **~08:45
  America/Denver** (after the 08:00 run, before the 09:30 watchdog).
  1. Check Sent first: today's "Kiwi's Weekend Guide" to
     `you@example.com`. If found → one-line all-clear, STOP.
  2. If not in Sent and a pending Kiwi's Weekend Guide Gmail-send approval
     (+send / users.messages.send) exists → alert the owner in chat that the
     guide is built and parked on his approval; tapping approve releases
     it; the 9:30 watchdog confirms delivery.
  3. If not in Sent and no approval pending → alert the owner the run needs
     attention; quote run-log status/notes; no speculation.
  - **Idempotency rule (hard):** the watch never triggers a send — alert
    only. No `+send`, no retries, no drafts.
- **`kiwis-corner-production-run`** (runonce 2026-09-24T08:00) and
  **`kiwis-corner-watchdog`** (runonce 2026-09-24T09:30) specs are in §3 of
  the same doc. 9/24 stays parallel-run (owner only) because 9/17 did not
  land clean.

**New in this audit:** `pipeline/approval_watch_logic.py::decide()` is a
pure transcription of the cron body's decision steps (docstring quotes the
source steps), so the logic is unit-testable without Gmail. Tested on all
four input combinations.

---

## 4. Calendar-conflict 3 tiers — old vs new

**Old behavior (pre-2026-09-14):** a single conflict flag; an all-day event
(e.g. the "49ers game" all-day placeholder) would have invalidated the day
and could suppress its guide events.

**New behavior** (`calendar_context.py::classify`, first-match-wins):
1. `all_day == True` → **informational**. Never flags, never suppresses,
   never invalidates the day (Google's exclusive all-day end date is
   clamped so a placeholder doesn't bleed into an extra day).
2. Timed + family-context keyword (`nanny, ava, travel, flight, visitor,
   visit, guest, birthday, anniversary, school, camp, daycare, potty`,
   substring, case-insensitive, title + description) → **context_notes**.
   Rendered as 🏡 day notes; never flags.
3. Other timed events → **hard_conflicts**. Only this tier can annotate a
   guide event, and only via `overlaps()` on genuinely intersecting
   windows (guide window = start_time + 120 min; missing calendar end =
   +60 min; midnight-crossing handled; guide events without a parseable
   start_time never flag — no guessing). Boundary rule: cal end == guide
   start is **not** an overlap.

`run.py::stage_enrich` iterates only `tiers["hard_conflicts"]` for flagging;
events are never dropped by the enrich stage.

---

## 5. Settled requirements intact — requirement → enforcing gate

| Settled requirement | Enforced by |
|---|---|
| ~40–65 verified Fri–Sun events | `verify.py` C1 (in-weekend) + `run.py` `EVENT_FLOOR=40` floor gate (pre-send halt) |
| Date-verification rule (non-negotiable) | `verify.py` C2 weekday, C3 freshness (current-season source), C4 operator confirmation; `verify.py --self-test` |
| No duplicate listings | `verify.py` C5 dedupe (same URL+date or same name+date fails) |
| Missing/blank price = hard FAIL | `template.require_price` (ValueError) + `run.py` pre-flight priceless check |
| Bespoke header paragraph every week | `--header-html` required in `run.py`; blank header → `ValueError` in `render_newsletter` |
| Per-day chronological order | `template.group_events` sorts by `time_key` (unparseable sorts last) |
| Price on every event; accurate links | `price_html` per event; pre-flight curls every unique href (403 accepted only for `axs.com`/`seatgeek.com`/`cpr.org`) |
| Footer branding | Pre-flight checks `FOOTER_BRAND == "Kiwi's Weekend Guide"` and rejects `LEGACY_BUG_BRAND`; `render_newsletter` raises on the legacy brand |
| No TODO/placeholder leakage | Pre-flight `_PLACEHOLDER_RE` scan (TODO/XXX/FIXME/lorem/`{{tokens}}`) |
| Never claim "sent" without proof | `send_hardened.py` two-evidence rule + `run.py` log integrity regex |
| No double-sends | Pre-send Sent search → `already_sent`; idempotency key `<run-date>::<recipient>` logged |
| Failed send never exits 0 | `run.py` raises `PipelineHalt` after writing the log (**added in this audit**) |
| No silent fallback send path | `run.py::load_send_module` raises loud `ImportError` on missing/differing `send_hardened.py` |
| Unknown send status → halt | `run.py::stage_send` treats unknown status as failed (`PipelineHalt`) |
| Parallel-run recipient policy | `--recipient` default `you@example.com`; second recipient excluded until the legacy sender is retired (`RUNBOOK.md` §4) |
| Multi-day events consolidate | `template.group_events` → "All Weekend" section |
| Tagging + kid-friendly callouts | category pill tags; `_kid_badge_html` 'Family pick' |
| Watchdog + approval nudge | `approval_watch.md` cron specs (8:45 watch, 9:30 watchdog) |
| Dry-run never sends | `send_newsletter` dry_run short-circuits before Step 3 |

---

## 6. How each item was verified — tests + results

**Commands run:**
```
cd ~/workspace/kiwis-corner/pipeline && python3 -m unittest discover -s tests
python3 ~/workspace/kiwis-corner/verify.py --self-test
```
**Results: 94/94 tests pass** (71 pre-existing, all still green; 23 new),
**verify self-test PASSED** (City Park incident record rejected on freshness
+ operator gates; good record accepted; dedupe regression passed).

New tests, by property:

- **(a) A run CANNOT claim "sent"** — `tests/test_send_gate_e2e.py`:
  `test_send_with_id_but_no_post_send_proof_fails` (mock send returns
  `msg_unverified1`, post-send search empty → `SendResult.status ==
  "failed"`, `run-log.md` written with zero standalone "sent" matches and
  `Status: `failed``, `run.main` raises `PipelineHalt`);
  `test_send_returning_no_id_fails` (send → `None` → same assertions);
  `test_verified_send_still_succeeds` (true two-evidence send → `"sent"`,
  exit 0, log records the message ID — the gate is not a blanket failure).
  All drive the real `run.main` end-to-end (verify → enrich → render →
  pre-flight → send → log); the real `CliGmailAdapter` subprocess path is
  never exercised (mock injected via `send_hardened.CliGmailAdapter`);
  the pre-flight link check is stubbed to 200 (no network) but still runs
  (41 unique hrefs asserted).
- **(b) Idempotency** — `tests/test_send_gate_e2e.py`:
  `test_second_send_blocked_as_already_sent` (send stage driven twice;
  second returns `"already_sent"`; `send()` invoked exactly once total);
  `test_dry_run_never_calls_send`.
- **(c) Approval-wait alert** — `tests/test_approval_watch_logic.py`:
  `test_all_four_input_combinations`, `test_decisions_are_a_closed_set`,
  `test_sent_in_sent_always_wins` (pending approval cannot override an
  all-clear), `test_module_documents_its_source` (transcription stays
  linked to `approval_watch.md`).
- **(d) Calendar tiers** — `tests/test_calendar_tiers.py` (12 tests):
  all-day "49ers game" placeholder → informational only;
  "Nanny's off — Riley day" / "Jordan visiting" → context_notes;
  "Dentist appointment" → hard_conflicts; boundary (cal end == guide
  start) is not an overlap; end-to-end through `run.stage_enrich` only
  the genuine dentist overlap is flagged (context-tier overlap and the
  all-day placeholder day flag nothing); enrich never drops events;
  context notes reach day notes; `run.stage_render` renders every guide
  event with no conflict annotations on a clean calendar and annotates
  only the flagged one.
- **Notes-contract regression** — `tests/test_send_hardened.py`:
  `test_no_non_sent_notes_contain_standalone_sent` (exhausts every
  non-"sent" result path; fails if any future notes edit reintroduces
  the standalone word), `test_sent_notes_may_say_sent` (the one allowed
  exception).

**Pre-existing failures fixed, not hidden:**
1. `test_run.TestSendContract.test_missing_module_raises_importerror` was
   failing in the baseline (it simulated a missing module by popping
   `sys.modules`, which stopped working once the real module existed on
   disk). Fixed the test's mechanism (patch `importlib.import_module` to
   raise) — product behavior unchanged and correct.
2. The two product fixes above (§1 failed-send halt, §2 notes rephrasing)
   were real bugs found during verification; tests were written against
   the fixed behavior, and the three notes-wording assertions updated
   accordingly.

---

## Gaps and open items (not closed by this audit)

1. The 8:45 approval-watch cron and the 9/24 production-run/watchdog crons
   are **specs in `approval_watch.md` only** — the coordinator must create
   them; this audit made no cron changes.
2. The e2e tests stub the pre-flight link check (`_curl_status` → 200) to
   avoid network dependence; real curl behavior in production is unchanged
   and untested here.
3. `verify.py` cannot verify that a researcher's `operator_confirmed=true`
   claim is truthful — only that the attestation exists (documented
   limitation, unchanged).
4. The approval watch's "list pending approval requests" capability is
   assumed to exist in the cron worker's toolset (per `approval_watch.md`
   §4); the coordinator should pin the tool name at cron creation.
5. All Gmail interaction in tests is mocked; the real `CliGmailAdapter`
   subprocess path (including the approval-timeout behavior) is covered
   only by parsing/unit tests, not a live Gmail call (intentionally —
   no real sends).
