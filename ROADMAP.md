# ROADMAP — from one family's newsletter to a product

## Where this stands

Kiwi's Weekend Guide is a proven, single-family system: one agent, one Gmail
account, one cron schedule, one Thursday email. It works because every hard
part is deterministic — verification gates, send integrity, idempotency —
while the genuinely agentic part (researching what's on this weekend) stays
with the agent.

## The next layer: a website + paid membership

The idea: other families get their own personalized weekend guide, as a
paid membership. That means turning the framework into a small platform:

1. **Onboarding** — a family signs up, enters home location, household
   (kids' ages, interests), recipient emails, preferred categories. This
   generates their config surface (the SETUP.md knobs, but as a form).
2. **Per-family agent runs** — the pipeline already parameterizes the family;
   the work is multi-tenancy: isolated run dirs, per-family cron schedules,
   per-family Gmail auth (each family connects their own Gmail).
3. **The website** — landing page, signup, member dashboard (past editions,
   preferences), billing.
4. **Deliverability** — one Gmail account per family works at small scale;
   real scale needs a proper sending domain, SPF/DKIM, and unsubscribe
   handling.

## Deliberately not decided yet

- Pricing, billing provider, and plan tiers
- Brand name for the consumer product
- Whether the agent runtime stays Meta Muse per family or moves to a hosted
   worker
- License for this repo (currently private, shared with friends/family)

This file is a stake in the ground, not a plan. The framework in this repo
is the asset; everything above builds on it.
