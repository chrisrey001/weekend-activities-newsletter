# Sending the newsletter through Resend (for Muse)

Written 2026-10-09 for the agent that runs `weekend-activities-newsletter`. The subscriber list
lives in Resend, not in a file. The website adds a contact only after the person clicks the link in
a confirmation email (double opt-in), so every contact in the segment below has proved they own the
address.

Everything here was taken from Resend's API reference on 2026-10-09 (https://resend.com/docs). Where
a sentence is quoted, it is quoted from those pages. Re-check the docs if a call fails.

## What is where in Resend

| Thing | Value | Who creates it |
|---|---|---|
| Sending domain | `kiwisweekendguide.fun` (region us-east-1) | Chris, in the Resend dashboard |
| From address | `Kiwi's Weekend Guide <newsletter@kiwisweekendguide.fun>` (sending only, no mailbox) | config |
| Reply-To | `kiwisweekendguide@gmail.com` (a real inbox Chris reads) | config |
| Segment | name `Newsletter (confirmed)` | the site's `confirm` function, on the first confirmation |
| Contact properties | `zip`, `kids_ages`, `dog`, `source`, `confirmed_at` (all strings) | same function |
| API key | `muse-pipeline`, **Full access** (broadcasts need more than "Sending access") | Chris; stored in the pipeline's env, never in git |

Resend's model (docs, Contacts introduction): Contacts are one global list; a Contact "can be in
zero, one, or multiple Segments"; Segments are "a tool to group and manage your Contacts for sending
Broadcasts". Broadcasts are sent to a segment: on Create Broadcast, `segment_id` is "required".
Contacts who unsubscribe are skipped automatically: "Contacts who are globally unsubscribed are
excluded from Broadcasts even if they belong to the target Segment."

Free plan (Resend pricing page, read 2026-10-08 for LAUNCH-PLAN.md): 3,000 emails/month, 100/day,
broadcasts to up to 1,000 contacts.

## The weekly send, step by step

All requests: `Authorization: Bearer $RESEND_API_KEY`, `Content-Type: application/json`.

### 1. Find the segment id (once; cache it)

```
GET https://api.resend.com/segments
-> { "object": "list", "has_more": false, "data": [ { "id": "...", "name": "Newsletter (confirmed)", "created_at": "..." } ] }
```
Pick the entry whose `name` is `Newsletter (confirmed)`. If it is missing, nobody has confirmed yet:
stop, there is no one to send to.

### 2. Screen out-of-area zips BEFORE sending

The website does not check zip codes. `served-zips.md` in the site repo is the list of 100 served
codes. List the segment's contacts and compare each contact's `zip` property:

```
GET https://api.resend.com/segments/{segment_id}/contacts
```
(Returns the contacts in that segment; each contact has `properties`, e.g.
`"properties": { "zip": { "value": "80110", "type": "string" } }`, see Get Contact.)

Contacts whose zip is **not** in `served-zips.md` must not get the Denver edition. Two options:
- simplest: remove them from the segment (`DELETE /contacts/{email}/segments/{segment_id}`) and keep
  them on a waitlist segment you create (`POST /segments` with `{"name": "Waitlist (out of area)"}`,
  then `POST /contacts/{email}/segments/{waitlist_id}`); or
- move the screening into the site's `confirm` function later (it has the zip at confirm time).

Also count out-of-area signups for the "Out-of-area demand" trigger in the site README (more than 10
means it is time to think about expanding).

### 3. Create the broadcast

```
POST https://api.resend.com/broadcasts
{
  "name": "Kiwi's Weekend Guide 2026-10-16",          // internal only
  "segment_id": "<segment id from step 1>",
  "from": "Kiwi's Weekend Guide <newsletter@kiwisweekendguide.fun>",
  "reply_to": "kiwisweekendguide@gmail.com",
  "subject": "Your weekend, sorted: Oct 16-18",
  "html": "<full rendered edition>",
  "text": "<plain-text version>"                        // optional; omitted = generated from html
}
-> { "object": "broadcast", "id": "<broadcast id>" }
```
Docs: "html: The HTML version of the message. You can include Contact Properties in the body of the
Broadcast." Placeholders Resend fills per recipient: `{{{contact.first_name|there}}}`,
`{{{contact.email}}}`, and the unsubscribe link `{{{RESEND_UNSUBSCRIBE_URL}}}`. Write them exactly
like that (three braces), and do not run them through the pipeline's own placeholder pre-flight
check, or whitelist them there.

### 4. Send it

```
POST https://api.resend.com/broadcasts/{broadcast_id}/send
{}                              // or { "scheduled_at": "2026-10-16T14:00:00Z" } to schedule
-> { "id": "<broadcast id>" }
```
Docs: "You can send broadcasts only if they were created via the API." `scheduled_at` takes ISO 8601
or natural language ("in 1 min").

### 5. Record the result

Treat the `id` from step 4 as the "sent" proof (the pipeline's sent-first rule). Resend's dashboard
shows per-recipient delivery under Broadcasts.

## Required footer (every public edition)

```html
<p style="font-size:12px;color:#6B6270">
  You get this because you confirmed your email at kiwisweekendguide.fun.
  <a href="{{{RESEND_UNSUBSCRIBE_URL}}}">Unsubscribe</a> with one click, or reply STOP.
  Kiwi's Weekend Guide, Englewood, CO.
  <!-- TODO before wider marketing: add a postal mailing address here (CAN-SPAM). -->
</p>
```
- `{{{RESEND_UNSUBSCRIBE_URL}}}` is Resend's placeholder: "Resend will automatically handle
  unsubscribe requests once you include {{{RESEND_UNSUBSCRIBE_URL}}} in your email." Resend generates
  a unique link per recipient and marks the contact unsubscribed; later broadcasts skip them.
- "Reply STOP": replies go to kiwisweekendguide@gmail.com. Whoever reads that inbox must then mark
  the contact unsubscribed by hand: `PATCH /contacts/{email}` with `{"unsubscribed": true}`.
- Postal address: not added yet (friends-only launch). Decision 2026-10-08: must be fixed before any
  wider marketing.

## Do not

- Do not send the public edition through Gmail with subscriber addresses in To/Bcc. Resend
  broadcasts handle one-message-per-recipient and the unsubscribe link.
- Do not put a `re_` key in config files that are committed. The private `config.yaml` is
  git-ignored; keep the key in an environment variable the pipeline reads.
- Do not send from an `@gmail.com` address through Resend (Gmail's anti-spoofing rejects it).

## Status

- Written against the docs only. **Not yet exercised:** no broadcast has been created or sent through
  this path. The first real send should be to the Rey household only (a segment with just those
  contacts), checked in the Resend dashboard, before it goes to friends.
