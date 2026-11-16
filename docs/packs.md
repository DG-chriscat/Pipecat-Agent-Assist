# Use case packs

PRD IN-4, DC-10, AP-6, AP-11, AP-12. Cards P2-3 (intake) and P4-2 (assist).

A pack bundles everything one kind of call needs: the intake questions, the
triggers that activate it, the connectors the assist may call, card templates, a
call plan checklist, next best step rules, and STT keyterms. Adding a use case means
adding `packs/<id>.yaml`, not changing code.

Code: `bot/agent_assist_bot/packs.py` (pydantic models, loader, matching helpers).
Tests: `bot/tests/test_packs.py` (intake), `test_packs_v2.py` (assist sections and
validation), `test_packs_seed.py` (demo personas against the seed).

## v1 packs

| Pack | Triggers (examples) | Connectors | Cards | Next best steps |
| --- | --- | --- | --- | --- |
| `claims_status` | where is my claim, denied, prior authorization, still pending, CO-197 | members, claims, referrals, prior_authorizations, interactions, kb_articles | claims table, denied claim, prior authorizations, referrals, claims article | retro auth review (CO-197), provider info follow up (pending_info), out of network appeal (CO-242), prior auth pending |
| `account_payments` | charged twice, double charge, refund, premium, autopay | members, payments, kb_articles | payment history, duplicate charge alert, billing article | duplicate premium refund |
| `general_question` | fallback: anything the other packs do not match | kb_articles | KB article | none |

Demo personas (docs/db.md) and what they hit:

| Persona | Pack | Hits |
| --- | --- | --- |
| Maria Alvarez, EVM100231 | claims_status | CLM-900001 denied CO-197: `retro_auth_review` |
| James Chen, EVM100232 | account_payments | second premium autopay draft this month: `duplicate_charge` alert, `duplicate_premium_refund` |
| Aisha Patel, EVM100233 | claims_status | CLM-900003 pending_info: `provider_info_follow_up` |
| Linda Nguyen, EVM100234 | claims_status, general_question | CLM-900005 denied CO-242: `out_of_network_appeal`; "what is an EOB": KB-CL-010 |
| Daniel Okafor, EVM100235 | claims_status | PA-500101 pending: `prior_auth_pending` |

`DEFAULT_PACK` (`claims_status`) is the intake pack unless `INTAKE_PACK` is set.

## Format

Top level keys. Intake keys are required; every assist key is optional, so an
intake only pack still loads.

| Key | Type | What |
| --- | --- | --- |
| `id` | string | Pack id. Must match the file name (`load_packs` checks). |
| `name` | string | Display name. |
| `identity`, `reason`, `readback` | `{prompt}` | Intake step wording (IN-3). |
| `questions` | list | Intake questions: `key` (snake_case, not a core key), `ask`, `description`, `required`. Each key is saved on `calls.intake`. |
| `triggers` | `{phrases, fallback}` | Phrases that activate the pack. `fallback: true` marks the one pack that takes everything else. |
| `allowed_connectors` | list | Connector names from the P4-1 catalog (docs/connectors.md). The assist LLM only gets these tools. |
| `keyterms` | list | Fixed Deepgram STT keyterms (max 100): plan names, provider names, the `EVM` member ID prefix, denial codes and terms. Never per member. |
| `card_templates` | list | Cards the assist can show (below). |
| `call_plan` | list | The checklist (AP-12, below). |
| `next_best_steps` | list | DC-10 rules (below). |

### Phrases

Used by `triggers.phrases`, rule `triggers`, and `done_when.phrases`. Matching is
case insensitive, on whole words, and tolerates up to 3 punctuation or space
characters between words, so `CO-197` matches "co 197" and `where's my claim`
matches "wheres my claim". `denied` does not match inside "undeniedly". Each match
reports the phrase and its span in the line (to underline trigger words).

### Facts

A fact is `intake.<key>` (a core intake key, `full_name`, `dob`, `member_id`,
`reason`, `summary`, or a pack question key) or `<connector>.<field>` (a field of an
allowed connector's get result, see docs/connectors.md). `connector_facts("claims",
row)` turns a connector row into facts.

### card_templates

| Key | What |
| --- | --- |
| `id` | snake_case, unique in the pack. |
| `type` | `record`, `article`, or `alert` (AP-6). The call plan and next best step are their own sections. |
| `title` | Card title. |
| `connector` | One of `allowed_connectors`. |
| `fields` | Connector fields to show, in order. Must exist on the connector. |
| `summary` | Optional one line with `{field}` placeholders from `fields`. |
| `search` | Optional search arguments that select the rows, e.g. `{status: denied}`. |
| `source` | Source label on the card. Defaults to the connector's source (e.g. "Claims system"). |
| `detect` | Alerts only. `kind: duplicate`: rows matching `where`, grouped by the `same` fields and the `period` (`day` or `month`) of `date_field`; every row after the first in a group is a duplicate. `CardTemplate.detect.find(rows)` returns them. |

### call_plan

Ordered steps: `id`, `label`, and `done_when` with `phrases` (heard in the
transcript) and/or `actions` (accepted next best step action ids from this pack).
`pack.plan_ticks(transcript, accepted_actions)` returns the step ids that are done.
The agent can still tick or untick by hand (the portal owns that state).

### next_best_steps (DC-10)

| Key | What |
| --- | --- |
| `id` | snake_case, unique in the pack. |
| `triggers` | Phrases that make the rule a candidate. At least one. |
| `required_facts` | Facts that must all be known (not empty) before the rule can fire. |
| `when` | Optional `{fact: value}` conditions (case insensitive). Keys must be required facts. |
| `headline`, `why`, `script` | Templates with `{fact}` placeholders. Every placeholder must be a required fact, so a rendered card never has a gap. `script` is the "Say this" text. |
| `action` | `{id, label}`: the one primary action. Simulated in v1 (DC-9): accepting logs it on the call record and ticks the call plan. Action ids are unique across all packs. |
| `source` | Source label for the card, e.g. "Claims system, KB-CL-002". KB ids cited here must exist in the seed (tested). |
| `priority` | Integer, higher ranks first when several rules apply. Default 0. |

`pack.rules_triggered(line)` returns the rules whose triggers are in a line;
`rule.applies(facts)` checks required facts and `when`; `rule.render(facts)` fills
the templates. The LLM only supplies record facts; the rule decides the headline,
script, and action. No clinical advice is ever in a rule.

## Loading and selecting

- `load_pack(id_or_path)` loads and validates one pack; errors raise `PackError`
  with the field path and reason.
- `load_packs()` loads every `packs/*.yaml` and also checks that ids match file
  names, at most one pack is the fallback, and action ids are unique across packs.
- `select_pack(line, packs)` returns the pack with the most trigger matches in the
  line (ties: first by file name), else the fallback pack.

Validation (on load):

- connector names exist in the P4-1 catalog, and card connectors are allowed;
- card fields, summary placeholders, and `detect` fields exist on the connector;
- rule facts reference intake keys or allowed connector fields, `when` keys are
  required facts, and template placeholders are required facts;
- ids are unique (cards, plan steps, rules, actions; actions across packs);
- plan `done_when.actions` refer to actions in the pack.

## Full example: `account_payments`

```yaml
id: account_payments
name: Account and payments

identity:
  prompt: >-
    Ask for the caller's full name, date of birth, and member ID. ...
reason:
  prompt: >-
    Ask, in one short question, what they are calling about today.
questions:
  - key: charge_date
    ask: About when was the charge you're calling about?
    description: The date or month of the charge in the caller's words, or "unknown".
  - key: charge_amount
    ask: And roughly how much was it?
    description: The amount the caller mentions, for example 610 dollars, or "unknown".
  - key: payment_concern
    ask: What went wrong with the payment, for example charged twice, wrong amount, or a refund you're waiting on?
    description: Short description of the problem, such as charged twice, wrong amount, or refund.
readback:
  prompt: >-
    Read back a short summary in one or two sentences ... Then ask if that is right.

triggers:
  phrases: [charged twice, double charge, double charged, charged me twice, two charges,
            duplicate charge, refund, premium, autopay, payment, my bill]

allowed_connectors: [members, payments, kb_articles]

keyterms: [EVM, PAY, Evergreen HMO Silver, Evergreen HMO Gold, Summit PPO Gold,
           Harbor EPO Bronze, Summit HDHP, Evergreen Medicare Advantage Plus, premium,
           autopay, refund, double charge, charged twice, draft, copay, deductible]

card_templates:
  - id: payment_history
    type: record
    title: Payment history
    connector: payments
    fields: [payment_id, paid_at, amount, kind, method, status]
  - id: duplicate_charge
    type: alert
    title: Possible duplicate premium charge
    connector: payments
    search: {kind: premium}
    fields: [payment_id, paid_at, amount, method, status, reference]
    summary: "{payment_id} drafted {amount} on {paid_at}, a second premium this month"
    detect:
      kind: duplicate
      same: [kind, amount]
      date_field: paid_at
      period: month
      where: {kind: premium, status: posted}
  - id: billing_article
    type: article
    title: Knowledge base
    connector: kb_articles
    search: {category: billing}
    fields: [article_id, title, summary]

call_plan:
  - id: confirm_charges
    label: Confirm the charges on the account
    done_when:
      phrases: [i can see, i see two, i see the, both payments, two payments, two drafts]
  - id: explain_cause
    label: Explain what caused the charge
    done_when:
      phrases: [autopay, drafted twice, duplicate draft]
  - id: submit_refund
    label: Submit the refund request
    done_when:
      actions: [submit_premium_refund]
  - id: set_expectations
    label: Tell the caller when the refund posts
    done_when:
      phrases: [business days, posts back, back to your account]

next_best_steps:
  - id: duplicate_premium_refund
    priority: 10
    triggers: [charged twice, double charge, double charged, charged me twice,
               two charges, duplicate charge, refund]
    required_facts: [payments.payment_id, payments.amount, payments.paid_at, payments.kind]
    when: {payments.kind: premium}
    headline: Refund the duplicate premium draft {payments.payment_id}
    why: >-
      Autopay drafted the {payments.amount} premium twice this month. The second
      draft, {payments.payment_id} on {payments.paid_at}, is the duplicate.
    script: >-
      You're right, I can see your premium was drafted twice this month. I'm
      submitting a refund for the second charge of {payments.amount} now. It should
      post back to your account in 5 to 7 business days.
    action: {id: submit_premium_refund, label: Submit refund request}
    source: Billing system, KB-BL-001
```

How the assist uses it for James Chen (EVM100232), as `test_packs_seed.py` runs it:

```python
packs = load_packs()
line = "I was charged twice for my premium this month"
pack = select_pack(line, packs)  # account_payments
alert = pack.card_template("duplicate_charge")
rows = (await session.call("payments_search", alert.search))["items"]
dup = alert.detect.find(rows)[0]  # the second November draft
facts = connector_facts("payments", dup)
rule = next(r for r in pack.rules_triggered(line) if r.applies(facts))
card = rule.render(facts)  # headline, why, script, action_id, action_label, source
```

## What uses what

- Intake (P2-3): `identity`, `reason`, `questions`, `readback`.
- Assist engine (P4-3, docs/assist.md): triggers, allowed connectors, rules, card
  templates, call plan.
- Portal (P4-4): card templates (fields, source), call plan labels.
- STT: the listen branches boost every pack's `keyterms` (deduped, max 100) through
  `DeepgramSTTService.Settings(keyterm=...)` (P4-3). Intake STT does not use them yet.
