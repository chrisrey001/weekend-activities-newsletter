"""Verified send gate for the Kiwi's Weekend Guide newsletter.

Contract (imported lazily by pipeline/run.py -- the interface is validated
by run.py's load_send_module(); do not rename anything without updating it):

    from dataclasses import dataclass

    @dataclass
    class SendResult:
        status: str        # one of: "sent" | "already_sent" | "failed" | "dry_run"
        message_id: str | None
        notes: str

    def send_newsletter(*, html_body: str, subject: str, recipient: str,
                        run_id: str, dry_run: bool = False,
                        sender: str | None = None,
                        gmail=None) -> SendResult

Hardening (2026-09-17 incident): the 9/17 production run parked on an
unanswered connector approval while the run log falsely recorded "sent" with
no message ID. This module makes that impossible by construction:

  * Step 1 -- idempotency: today's Sent mail is searched FIRST. If this
    week's guide is already in Sent, the call returns "already_sent" and
    never attempts another send.
  * Step 2 -- dry_run short-circuits before any send attempt.
  * Step 2b -- in-flight claim (2026-10-01): an atomic claim file is taken
    before the send and released when the attempt resolves. A concurrent
    path finding a fresh claim returns "failed" WITHOUT touching the Gmail
    adapter -- the Step-1 mailbox check cannot see a send that is parked on
    an unanswered approval, and without this guard two paths each raise
    their own approval card for the same email (exactly what happened on
    2026-10-01). Stale claims (>30 min, e.g. a crashed holder) are
    re-verified against Sent and taken over, never trusted blindly.
  * Step 3 -- the Gmail send must return a real message ID. No ID (None,
    empty, or unparseable output) => "failed".
  * Step 4 -- post-send confirmation: Sent is re-searched and the returned
    message ID must be found there, dated today, addressed to the recipient.
    Only then => "sent". Anything else => "failed" with an explicit
    DO-NOT-RETRY note (a retry could double-send; the message may already
    exist in Sent).

The status "sent" is returned from exactly ONE code path (the Step-4
success). No other path returns it.

ASSUMPTIONS (documented, per contract):
  1. "Today" is computed in America/Denver (the newsletter's send timezone).
  2. The gmail adapter exposes:
         send(to, subject, html_body, sender=None) -> str | None
             (Gmail message ID). `sender` is the full From value, e.g.
             "Kiwi <you@example.com>"; None uses the account default.
         search_sent(subject, recipient, date_str) -> list[dict]
     where each dict has keys "id", "to", "subject", "date" (the Date header
     as Gmail returns it, e.g. "Thu, 17 Sep 2026 08:12:00 -0600").
     date_str is "YYYY-MM-DD" in America/Denver.
  3. Fail-closed: if search_sent() raises (CLI failure, auth problem, ...),
     the send is ABORTED with status "failed". We never send blind when we
     cannot prove Sent state -- a failed search must not be read as
     "not sent".
  4. Subject match for idempotency is exact after strip + casefold. The
     pipeline generates the subject deterministically, so exact matching is
     correct and avoids false positives.
  5. Recipient match is case-insensitive and tolerant of display-name /
     multi-recipient To headers: recipient matches if it equals the To value
     or is contained in it, case-insensitively. (Gmail/CLI render "To" as
     "Name <addr>" or a comma list; a strict equality check would false-
     negative.)
  6. Default CLI adapter (gmail=None): shells out to `hatch_gws_cli`.
     - Any non-zero exit, timeout, or error-shaped output is a FAILURE
       (raised as GmailAdapterError), never success. A success-shaped JSON
       output without a non-empty "id"/"messageId" field is treated as a
       send with NO message ID (None) => "failed" in step 3, not an invented
       ID. We never fabricate a message ID.
     - Approval parking: if the connector holds the send for approval past
       the subprocess timeout, the process is killed and the send reports
       "failed". Gmail performs nothing on an expired approval (observed
       2026-09-17: "approval_expired ... The action was not performed"), so
       a timed-out send is safe to report as failed. The 8:45 approval-watch
       cron (see approval_watch.md) exists precisely to alert the owner when the
       send is parked on his approval instead of silently missing it.
  7. run_id is for traceability only; it is echoed into notes, never into
     the message or the matching logic.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Constants

# Default send timezone. Override per-family via configure_timezone() --
# run.py sets it from config.yaml. Kept module-level because the send gate
# computes "today" in several helpers.
TIMEZONE = ZoneInfo("America/Denver")
DENVER = TIMEZONE  # legacy alias


def configure_timezone(name):
    """Set the send timezone (IANA name, e.g. "America/Chicago")."""
    global TIMEZONE, DENVER
    TIMEZONE = ZoneInfo(name)
    DENVER = TIMEZONE

STATUSES = ("sent", "already_sent", "failed", "dry_run")

# Subprocess timeouts (seconds). The send timeout must be long enough for a
# normal Gmail API round-trip but short enough that a send parked on an
# unanswered connector approval fails LOUDLY instead of hanging the run.
# (Observed 2026-09-17: an approval-expired +send took ~10 min to return.)
SEND_TIMEOUT_S = 120
LIST_TIMEOUT_S = 45
GET_TIMEOUT_S = 45

_CLI = "hatch_gws_cli"

# In-flight send claim (2026-10-01 incident): the Step-1 mailbox check only
# sees *completed* sends. A send parked on an unanswered connector approval
# is invisible to it, so two concurrent execution paths (the cron worker and
# a descendant subagent on 2026-10-01) each passed Step 1 and each raised
# their own approval card for the same email. The claim file covers that
# in-flight window: it is created atomically (O_CREAT|O_EXCL) between the
# dry-run short-circuit and the send, held only for the duration of the send
# attempt, and released when the attempt resolves (sent or failed). A second
# path that finds a fresh claim returns "failed" WITHOUT invoking the Gmail
# adapter -- no second approval card, ever. Override the directory in tests
# (or per deployment) via the KIWIS_SEND_CLAIM_DIR environment variable.
CLAIM_DIR = os.environ.get(
    "KIWIS_SEND_CLAIM_DIR",
    os.path.join(os.path.expanduser("~"), ".kiwis-corner-send-claims"),
)
# A crashed holder must not block a later run forever: claims older than
# this are treated as stale, re-verified against Sent, and taken over.
CLAIM_TTL_S = 30 * 60


# ---------------------------------------------------------------------------
# Public contract

@dataclass
class SendResult:
    status: str        # one of: "sent" | "already_sent" | "failed" | "dry_run"
    message_id: Optional[str]
    notes: str


class GmailAdapterError(RuntimeError):
    """Raised by adapters when the underlying Gmail operation fails.

    send_newsletter() treats this as "failed" -- never as success and never
    as permission to skip the Sent verification.
    """


def send_newsletter(*, html_body: str, subject: str, recipient: str,
                    run_id: str, dry_run: bool = False,
                    sender: str | None = None,
                    gmail=None) -> SendResult:
    """Send the newsletter with idempotency + verified-send gating.

    `sender` is the From display identity, e.g. "Kiwi <you@example.com>".
    None keeps the account default. (Newsletter-only branding: the shared agent
    alias keeps its own display name for other workflows.)

    Steps: (1) idempotency check against today's Sent; (2) dry-run short-
    circuit; (2b) in-flight claim so concurrent paths can't each raise an
    approval card; (3) send, requiring a message ID; (4) post-send
    confirmation that the ID is in Sent, dated today, addressed to
    recipient. Only step 4 success returns status "sent".
    """
    adapter = gmail if gmail is not None else CliGmailAdapter()
    today = _today_denver()
    date_str = today.isoformat()  # "YYYY-MM-DD"

    # -- Step 1: idempotency --------------------------------------------
    try:
        candidates = adapter.search_sent(subject=subject,
                                         recipient=recipient,
                                         date_str=date_str)
    except GmailAdapterError as exc:
        return SendResult(
            "failed", None,
            f"send aborted: pre-send mailbox-listing search failed ({exc}) "
            f"-- duplicate risk unknown; manual review required "
            f"(run_id={run_id})")
    except Exception as exc:  # defensive: adapters must not crash the gate
        return SendResult(
            "failed", None,
            f"send aborted: search_sent raised {type(exc).__name__}: {exc} "
            f"(run_id={run_id})")

    for cand in candidates or []:
        if _is_same_send(cand, subject=subject, recipient=recipient,
                         today=today):
            return SendResult(
                "already_sent", cand.get("id"),
                f"duplicate prevented: today's guide already present in "
                f"delivered mail (message id {cand.get('id')}; "
                f"run_id={run_id})")

    # -- Step 2: dry run --------------------------------------------------
    if dry_run:
        return SendResult(
            "dry_run", None,
            f"dry run: no send attempted (run_id={run_id})")

    # -- Step 2b: in-flight claim -----------------------------------------
    # The Step-1 mailbox check cannot see a send parked on an unanswered
    # connector approval, so without this guard two concurrent paths each
    # raise their own approval card for the same email (2026-10-01: the cron
    # worker and a descendant subagent). A blocked path returns here WITHOUT
    # touching the Gmail adapter -- no second approval card, ever.
    blocked = _acquire_claim(adapter, subject=subject, recipient=recipient,
                             date_str=date_str, run_id=run_id)
    if blocked is not None:
        return blocked
    try:
        return _attempt_send(adapter, html_body=html_body, subject=subject,
                             recipient=recipient, sender=sender,
                             date_str=date_str, run_id=run_id)
    finally:
        _release_claim(subject=subject, recipient=recipient,
                       date_str=date_str, run_id=run_id)


def _attempt_send(adapter, *, html_body: str, subject: str, recipient: str,
                  sender: Optional[str], date_str: str,
                  run_id: str) -> SendResult:
    """Steps 3-4: send requiring a message ID, then post-send confirmation.

    Only called while holding the in-flight claim; the caller releases the
    claim when this returns or raises. "sent" is returned from exactly ONE
    code path below (the Step-4 success).
    """
    today = _today_denver()
    # -- Step 3: send, requiring a message ID ----------------------------
    try:
        message_id = adapter.send(to=recipient, subject=subject,
                                  html_body=html_body, sender=sender)
    except GmailAdapterError as exc:
        return SendResult(
            "failed", None,
            f"send failed: {exc} (run_id={run_id}) -- check the mailbox "
            f"before any retry")
    except Exception as exc:
        return SendResult(
            "failed", None,
            f"send failed: send() raised {type(exc).__name__}: {exc} "
            f"(run_id={run_id})")

    if not message_id or not str(message_id).strip():
        return SendResult(
            "failed", None,
            f"send returned no message ID (run_id={run_id}) -- send status "
            f"unknown; check the mailbox before retrying")

    message_id = str(message_id).strip()

    # -- Step 4: post-send confirmation (THE ONLY "sent" PATH) ------------
    try:
        after = adapter.search_sent(subject=subject, recipient=recipient,
                                    date_str=date_str)
    except GmailAdapterError as exc:
        return SendResult(
            "failed", message_id,
            "post-send verification missing: the message ID was not "
            f"confirmed in delivered mail on re-check ({exc}) -- DO NOT "
            "retry without checking the mailbox; manual review required "
            f"(run_id={run_id})")
    except Exception as exc:
        return SendResult(
            "failed", message_id,
            "post-send verification missing: the message ID was not "
            f"confirmed in delivered mail on re-check (re-search raised "
            f"{type(exc).__name__}: {exc}) -- DO NOT retry without checking "
            f"the mailbox; manual review required (run_id={run_id})")

    for cand in after or []:
        if _is_verified_send(cand, message_id=message_id, recipient=recipient,
                             today=today):
            return SendResult(
                "sent", message_id,
                f"verified in Sent (message id {message_id}; run_id={run_id})")

    return SendResult(
        "failed", message_id,
        "post-send verification missing: the message ID was not confirmed "
        "in delivered mail -- DO NOT retry without checking the mailbox; "
        f"manual review required (run_id={run_id})")


# ---------------------------------------------------------------------------
# Matching helpers (small, deterministic)

def _today_denver() -> date:
    return datetime.now(TIMEZONE).date()


def _parse_date_header(value: Any) -> Optional[date]:
    """Parse a Gmail Date header to a Denver date. None if unparseable."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        # No timezone info: assume the send timezone rather than UTC.
        dt = dt.replace(tzinfo=TIMEZONE)
    return dt.astimezone(TIMEZONE).date()


def _norm_subject(value: Any) -> str:
    return str(value or "").strip().casefold()


def _split_recipients(recipient: Any) -> list:
    """Split a comma/semicolon-separated recipient string into addresses.

    Since the legacy sender's retirement (2026-09-26) the newsletter goes to both
    both recipients in ONE message, so matching must require EVERY
    address present in the To header -- never just one of them.
    """
    return [p.strip() for p in re.split(r"[;,]", str(recipient or ""))
            if p.strip()]


def _to_matches(to_header: Any, recipient: str) -> bool:
    """Every recipient address must appear in the To header
    (case-insensitive), tolerant of 'Name <addr>' and multi-recipient
    headers. Order-insensitive."""
    to_v = str(to_header or "").strip().casefold()
    addrs = _split_recipients(recipient)
    if not to_v or not addrs:
        return False
    return all(addr.casefold() in to_v for addr in addrs)


def _is_same_send(candidate: dict, *, subject: str, recipient: str,
                  today: date) -> bool:
    """Idempotency predicate: is this Sent message today's guide to the
    recipient?"""
    if not isinstance(candidate, dict):
        return False
    if not candidate.get("id"):
        return False
    if _norm_subject(candidate.get("subject")) != _norm_subject(subject):
        return False
    if not _to_matches(candidate.get("to"), recipient):
        return False
    return _parse_date_header(candidate.get("date")) == today


def _is_verified_send(candidate: dict, *, message_id: str, recipient: str,
                      today: date) -> bool:
    """Post-send confirmation predicate: same message ID, dated today,
    addressed to the recipient (case-insensitive)."""
    if not isinstance(candidate, dict):
        return False
    if str(candidate.get("id") or "").strip() != message_id:
        return False
    if not _to_matches(candidate.get("to"), recipient):
        return False
    return _parse_date_header(candidate.get("date")) == today


# ---------------------------------------------------------------------------
# In-flight claim (Step 2b)

def _claim_key(subject: str, recipient: str, date_str: str) -> str:
    """Filesystem-safe claim filename for one send identity."""
    slug = re.sub(r"[^a-z0-9]+", "_",
                  f"{date_str}_{subject}_{recipient}".lower())
    return f"send_{slug.strip('_')[:120]}.json"


def _claim_path(subject: str, recipient: str, date_str: str) -> str:
    return os.path.join(CLAIM_DIR, _claim_key(subject, recipient, date_str))


def _read_claim(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and data.get("run_id"):
            return data
    except (OSError, ValueError):
        pass
    return None


def _claim_is_fresh(data: dict) -> bool:
    try:
        age = time.time() - float(data.get("claimed_at", 0))
    except (TypeError, ValueError):
        return False
    return 0 <= age < CLAIM_TTL_S


def _acquire_claim(adapter, *, subject: str, recipient: str, date_str: str,
                   run_id: str) -> Optional[SendResult]:
    """Take the in-flight claim for this send. None = acquired, proceed.

    Any non-None SendResult must be returned immediately WITHOUT touching
    the Gmail adapter -- that is the whole point: a second concurrent path
    must never raise its own approval card for a send another path is
    already attempting (2026-10-01 incident).
    """
    path = _claim_path(subject, recipient, date_str)
    try:
        os.makedirs(CLAIM_DIR, exist_ok=True)
    except OSError:
        pass  # best effort; the atomic create below is the real gate

    fd = None
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    except OSError as exc:
        # Claim dir unusable: fail closed rather than risk a duplicate card.
        return SendResult(
            "failed", None,
            f"send not attempted: could not create in-flight claim ({exc}); "
            f"refusing to raise an approval card blind (run_id={run_id})")
    if fd is not None:
        try:
            os.write(fd, json.dumps({
                "run_id": run_id,
                "claimed_at": time.time(),
                "subject": subject,
                "recipient": recipient,
                "date": date_str,
            }).encode("utf-8"))
        finally:
            os.close(fd)
        return None

    existing = _read_claim(path)
    if existing is not None and _claim_is_fresh(existing):
        return SendResult(
            "failed", None,
            "send not attempted: an identical send is already in flight "
            f"(claimed by run_id={existing['run_id']}); no duplicate "
            f"approval card was raised (run_id={run_id}). If that attempt "
            "failed, its claim expires "
            f"{int(CLAIM_TTL_S / 60)} min after it was taken.")

    # Stale or unreadable claim: the holder crashed or never finished.
    # Re-verify Sent before taking over -- the first attempt may have
    # landed after all.
    try:
        candidates = adapter.search_sent(subject=subject, recipient=recipient,
                                         date_str=date_str)
    except Exception as exc:
        return SendResult(
            "failed", None,
            "send not attempted: stale in-flight claim present and Sent "
            f"re-verification failed ({exc}) -- duplicate risk unknown; "
            f"manual review required (run_id={run_id})")
    today = _today_denver()
    for cand in candidates or []:
        if _is_same_send(cand, subject=subject, recipient=recipient,
                         today=today):
            try:
                os.unlink(path)
            except OSError:
                pass
            return SendResult(
                "already_sent", cand.get("id"),
                "send not attempted: stale claim found but today's guide is "
                "already in delivered mail "
                f"(message id {cand.get('id')}); treating as already sent "
                f"(run_id={run_id})")
    # Still clear: take over the stale claim and proceed. Done iteratively
    # (not recursively) so a failed unlink can't loop forever.
    try:
        os.unlink(path)
    except OSError:
        pass
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError as exc:
        return SendResult(
            "failed", None,
            f"send not attempted: could not take over stale in-flight "
            f"claim ({exc}); refusing to raise an approval card blind "
            f"(run_id={run_id})")
    try:
        os.write(fd, json.dumps({
            "run_id": run_id,
            "claimed_at": time.time(),
            "subject": subject,
            "recipient": recipient,
            "date": date_str,
        }).encode("utf-8"))
    finally:
        os.close(fd)
    return None


def _release_claim(*, subject: str, recipient: str, date_str: str,
                   run_id: str) -> None:
    """Release our in-flight claim. Only removes the file when it is ours."""
    path = _claim_path(subject, recipient, date_str)
    existing = _read_claim(path)
    if existing is not None and existing.get("run_id") == run_id:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Default adapter: hatch_gws_cli subprocess wrapper

def _run_cli(argv: list[str], timeout_s: int) -> subprocess.CompletedProcess:
    """Run the CLI. Raises GmailAdapterError on timeout or OS failure.

    NOTE: a non-zero exit does NOT raise here -- callers inspect the result
    because some CLI errors arrive with exit 0 and an error-shaped body
    (observed 2026-09-17).
    """
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout_s,
            stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise GmailAdapterError(
            f"CLI timed out after {timeout_s}s (command: "
            f"{' '.join(argv[:4])} ...). If a connector approval was pending, "
            f"it has expired and Gmail performed NO send.") from exc
    except OSError as exc:
        raise GmailAdapterError(f"could not execute CLI: {exc}") from exc


def _is_error_output(text: str) -> bool:
    t = (text or "").strip()
    if t.startswith("Error:"):
        return True
    if '"error"' in t or "'error'" in t or '"errorType"' in t:
        return True
    return False


def _parse_send_output(proc: subprocess.CompletedProcess) -> Optional[str]:
    """Extract the Gmail message ID from a +send result.

    Raises GmailAdapterError on error-shaped output (incl. non-zero exit).
    Returns None when the command succeeded but carried no parseable ID --
    the caller must treat that as "send returned no message ID".
    We NEVER invent an ID.
    """
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    if proc.returncode != 0 or _is_error_output(stdout) or \
            _is_error_output(stderr):
        detail = (stderr.strip() or stdout.strip())[:500]
        raise GmailAdapterError(f"+send failed: {detail or 'no output'}")
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return None  # success-shaped exit, unparseable body -> no ID
    if isinstance(payload, dict):
        for key in ("id", "messageId", "message_id"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def _gmail_query_escape(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def _sent_query(subject: str, date_str: str) -> str:
    """Build a Gmail search query: this subject, in Sent, dated date_str.

    Gmail date operators accept YYYY/M/D. Both after: and before: bound the
    search to the single Denver day so stale weeks can't false-positive.
    """
    day = date.fromisoformat(date_str)
    nxt = day + timedelta(days=1)
    after = f"{day.year}/{day.month}/{day.day}"
    before = f"{nxt.year}/{nxt.month}/{nxt.day}"
    return (f'in:sent subject:"{_gmail_query_escape(subject)}" '
            f"after:{after} before:{before}")


def _json_cli(*argv: str, timeout_s: int) -> Any:
    """Run a CLI command expected to return JSON on stdout.

    Raises GmailAdapterError on any failure -- callers must NOT treat this
    as "no results".
    """
    proc = _run_cli(list(argv), timeout_s)
    stdout = (proc.stdout or "").strip()
    if proc.returncode != 0 or _is_error_output(stdout) or \
            _is_error_output(proc.stderr or ""):
        detail = ((proc.stderr or "").strip() or stdout)[:500]
        raise GmailAdapterError(f"CLI failed: {detail or 'no output'}")
    try:
        return json.loads(stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        raise GmailAdapterError(
            f"CLI returned unparseable JSON: {stdout[:200]}") from exc


def _headers_to_dict(payload: Any) -> dict:
    """Extract lowercase header-name -> value from a messages.get response.

    Handles the raw-API shape (payload.headers: [{name, value}, ...]) and,
    defensively, a flattened dict shape.
    """
    if isinstance(payload, dict) and isinstance(payload.get("payload"), dict):
        headers = payload["payload"].get("headers") or []
    elif isinstance(payload, dict):
        headers = payload.get("headers") or []
    else:
        headers = []
    out: dict[str, str] = {}
    for h in headers:
        if isinstance(h, dict) and h.get("name"):
            out[str(h["name"]).lower()] = str(h.get("value") or "")
    return out


class CliGmailAdapter:
    """Default adapter: shells out to `hatch_gws_cli`.

    send() raises GmailAdapterError on any CLI-level failure (non-zero exit,
    timeout, error-shaped output such as an expired connector approval) and
    returns None only when the command reported success without a parseable
    message ID. search_sent() raises GmailAdapterError on any CLI failure
    (fail-closed: the orchestrator aborts instead of sending blind).
    """

    def __init__(self, cli: str = _CLI,
                 send_timeout_s: int = SEND_TIMEOUT_S,
                 query_timeout_s: int = LIST_TIMEOUT_S) -> None:
        self.cli = cli
        self.send_timeout_s = send_timeout_s
        self.query_timeout_s = query_timeout_s

    # -- adapter interface ------------------------------------------------
    def send(self, to: str, subject: str, html_body: str,
             sender: str | None = None) -> Optional[str]:
        argv = [self.cli, "gmail", "+send", "--to", to, "--subject", subject,
                "--body", html_body, "--html", "--format", "json"]
        if sender:
            # Full From value, e.g. "Kiwi <you@example.com>".
            # Verified 2026-09-28 via draft: the CLI preserves the display
            # name in the sent message's From header.
            argv += ["--from", sender]
        proc = _run_cli(argv, self.send_timeout_s)
        return _parse_send_output(proc)

    def search_sent(self, subject: str, recipient: str,
                    date_str: str) -> list[dict]:
        query = _sent_query(subject, date_str)
        listing = _json_cli(
            self.cli, "gmail", "users", "messages", "list",
            "--params", json.dumps({"userId": "me", "q": query,
                                    "maxResults": 10}),
            "--format", "json",
            timeout_s=self.query_timeout_s)
        messages = (listing or {}).get("messages") or []
        results: list[dict] = []
        for m in messages:
            mid = (m or {}).get("id")
            if not mid:
                continue
            detail = _json_cli(
                self.cli, "gmail", "users", "messages", "get",
                "--params", json.dumps({
                    "userId": "me", "id": mid, "format": "metadata",
                    "metadataHeaders": ["To", "Subject", "Date"]}),
                "--format", "json",
                timeout_s=GET_TIMEOUT_S)
            headers = _headers_to_dict(detail)
            results.append({
                "id": mid,
                "to": headers.get("to", ""),
                "subject": headers.get("subject", ""),
                "date": headers.get("date", ""),
            })
        # Final client-side filter on recipient too (the query already binds
        # subject + date server-side). Subject/date filtering is done by the
        # orchestrator's _is_same_send/_is_verified_send, so we return all
        # candidates here and let the gate decide.
        _ = recipient  # kept in signature per contract
        return results
