# Knowledge article review (P5-11 Part B, recommend only)

Chris, Nov 17: for each demo, which knowledge base articles the assist shows today, and
whether they give the agent what they need at that moment: what to do next, timelines,
what to tell the caller, what not to promise, and compliance notes. **Nothing here
changes `kb_articles` or the seed.** Chris approves; a follow up card applies the
rewrites and new articles.

Sources read: the 15 articles in `server/agent_assist_server/db/seed.py`
(`_kb_articles`), the packs (`packs/*.yaml`: rule `source` labels, `claims_article`,
`billing_article`, `kb_article` templates, triggers), the article matcher
(`bot/agent_assist_bot/assist_ask.py` `kb_hits`: an article shows when the line names
words from its title or tags), the P5-4 run shelves (`docs/demo.md`, batch 3), and the
talk tracks (`server/agent_assist_server/demo_personas.yaml`).

## How articles reach the agent today

| Path | When | What the agent sees |
| --- | --- | --- |
| Hero source label | Every next best step names its articles in `source` (for example "Claims system, KB-CL-002 and KB-CL-009") | Only the ids, as text under the hero. No title, no content. |
| General question pack | A caller question that names an article's title or tag words (`_kb_answer`, no LLM) | One article tile: title and the first 160 characters of the body (`summary = left(body, 160)`). |
| Assist LLM | It may propose `claims_article` / `billing_article` / `kb_article` cards | An article tile, when it does. In the P5-4 runs it never did on the claims and payments calls. |
| Ask bar | The agent types a question with no record words | Up to two article tiles from the same title and tag matcher. |

Two things follow for every demo:

1. **On the claims and payments calls the agent never sees article content.** The hero
   cites KB-CL-002, KB-BL-001, and so on, but the shelf in all P5-4 runs had no article
   tile (Maria: denied claim and plan; James: payment history, plan, duplicate alert).
2. **Every article is 118 to 175 characters, two or three sentences.** The tile shows
   the first 160 characters, so the tile is the whole article. There is no room for
   what to tell the caller, timelines past one number, or what not to promise.

## Per demo findings

Rating: **Good** (gives the agent what they need), **Weak** (right topic, missing
pieces), **Gap** (nothing useful shows).

### Maria Lopez: MRI claim 70412 denied CO-197 (hero demo). Weak

Shows: hero source "KB-CL-002 and KB-CL-009" (ids only). No article tile.

- KB-CL-002 "Denial code CO-197: authorization absent" says the *provider* asks for a
  retro review within 60 days, "or file an appeal". The hero script says the *agent*
  opens the retro review today. The agent reads two different owners.
- Timeline: the 60 day window runs from the denial (processed 16 days before the seed
  date, so about 44 days left). Neither article says how to count it or what to say
  when the window has passed.
- What happens next is missing: how long the retro review takes, how the caller hears
  the result, and what happens if it is denied (the 180 day appeal clock from the
  original denial still runs, KB-CL-008).
- What not to promise: the hero says "If it is approved, the claim is reprocessed
  automatically and you do not need to file an appeal." No article tells the agent not
  to promise approval or a payment amount.
- Maria's history note says "the orthopedist's office said auth was handled". She has
  an approved **referral** (REF-700002 to Pine Hill Orthopedics), not an authorization
  for the MRI at Riverside Imaging. No article explains referral vs prior
  authorization, which is the question she is really asking on a third call.

### James Carter: duplicate premium draft PAY-000067. Good, two gaps

Shows: hero source "KB-BL-001" (id only). No article tile.

- KB-BL-001 "Duplicate premium charge" has the steps (confirm both, refund the second)
  and the timeline (5 to 7 business days). The hero script matches it.
- Gap: the cause. The call plan step "Explain what caused the charge" ticks on
  "autopay" or "drafted twice", but no article says why autopay drafts twice (for
  example a payment method change mid cycle; James changed his autopay bank 75 days
  ago, INT-000006) or how to stop it next month (KB-BL-002 covers changes after the
  25th but is not linked).
- What not to promise: bank overdraft or returned item fees. Callers ask; the article
  should say the plan does not reimburse bank fees (or what the policy is).

### Sarah Miller: stress test claim 31845 pending info, third call. Weak

Shows: hero source "KB-CL-007" (id only). No article tile.

- KB-CL-007 "Claims pending provider information" gives the step (contact the
  provider office) and the turnaround (10 business days after receipt). Good core.
- Gap: the repeat caller. This is her third call (INT-000004, INT-000005) and the tone
  tip fires. Nothing tells the agent how to own the follow up: a callback date, when to
  escalate after repeated contacts, or how to say "this is on us to chase, not you".
- Missing reassurance: her prior authorization PA-500102 is approved, so this is not an
  authorization problem. The article could tell the agent to check and say so.
- What not to promise: a date the provider will send the bill (outside the plan's
  control).

### Linda Nguyen: dermatology claim 52976 denied CO-242, Medicare Advantage. Weak, compliance risk

Shows: hero source "KB-CL-003 and KB-CL-008" (ids only). After Mark verified, the
appeal hero.

- KB-CL-003 "Denial code CO-242: out of network provider" speaks to HMO and EPO plans.
  Linda is on **Evergreen Medicare Advantage Plus**. The article says nothing about
  Medicare Advantage.
- KB-CL-008 "How to file an appeal" gives 180 days and "standard decisions take up to
  30 days", and the hero script repeats both to the caller. For Medicare Advantage
  members the appeal (reconsideration) rules and timelines are different from a
  commercial plan's, including for payment appeals. **The agent should not quote the
  commercial numbers to a Medicare member.** Compliance should supply the plan's
  Medicare Advantage wording before this demo is shown to a payer audience.
- Gap: her referral REF-700003 to Bayview Dermatology expired 40 days ago, before the
  visit. That is the likely real reason and the most useful thing the agent could say
  ("your referral had expired; if your doctor renews it ..."). No article covers an
  expired referral.
- Ask bar: "show referrals" finds REF-700003, but no article explains what to do with it.

### Mark Hill: urgent care claim 46183 in review, two members named Mark Hill. Gap

Shows: nothing from the knowledge base. No next best step rule fires for a claim
`in_review`; the claims tile comes only from the assist LLM, sometimes.

- KB-CL-001 "How to check the status of a claim" lists the statuses but no timelines
  and nothing to tell the caller.
- The caller asks "when will it be paid?". The agent has no number to give.
- Identity: nothing tells the agent that two members share the name and that the date
  of birth is what picked the record (the demo point). A short identity article would
  help on every call, not just this one.

### Aisha Patel: what is an EOB (claim 19374, $50 member share). Good, one noise card

Shows: article tile KB-CL-010 "Reading an Explanation of Benefits (EOB)" on "explanation
of benefits". In P5-4 the shelf also showed **KB-CL-007 "Claims pending provider
information"**, because "Is it a bill?" matches the tag "itemized bill".

- KB-CL-010 answers the question ("An EOB is not a bill") and names the fields. Good.
- Weak: it does not tell the agent how to walk the caller through *her* EOB (billed
  $320, allowed $210, member share $50), or to wait for the provider's bill before
  paying. Her $50 is the specialist copay on Evergreen HMO Silver; the article does not
  link to KB-BL-005, and the general question pack cannot read claims or plans.
- Noise: KB-CL-007 on an EOB question is wrong and costs the agent a glance. The
  matcher takes one tag word ("bill"); tags should be phrases that are specific.

### David Brooks: deductible and out of pocket maximum, HDHP with HSA. Weak

Shows: article tile KB-BL-005 "Copays, deductibles, and out of pocket maximum".

- KB-BL-005 defines the three terms. It does not cover a high deductible plan: on the
  Summit HDHP with HSA there are no copays before the deductible ($3,200), the member
  pays the allowed amount, and preventive care is covered before the deductible. Those
  are the answers David needs.
- The general question pack has only the `kb_articles` connector, so the agent cannot
  see his plan's deductible ($3,200) or out of pocket maximum ($7,000), or that his lab
  claim 27591 ($95) went to the deductible. The article says "the deductible", not his.
- The second hit for his question is KB-CL-003 (out of network) on the word "out": noise.

### Ben Cole: wrong DOB, knee surgery prior auth PA-500101 pending. Gap

Shows: nothing from the knowledge base. The rule `prior_auth_pending` cites
"Utilization management" only.

- No article on a pending prior authorization: the standard and urgent decision
  timelines, that the provider sends the clinical notes, how to ask for an expedited
  review, and the key thing not to say: do not tell the member to schedule, and do not
  promise approval.
- No article for the verify hero either (see Tom Reed).

### Tom Reed: not a member, records stay locked. Gap

Shows: the Verify caller hero ("No member matched the details from intake") and its
script line. Nothing from the knowledge base.

- The agent needs a short procedure: what can be shared with an unverified caller
  (general information only, no claims or member data), how to try once more (member ID
  from the card, date of birth), and how to close (callback once verified, or the
  member services number on the card). The verify hero has a script line but no source.

### Emily Stone: emergency guardrail. Gap

Shows: the call reaches the queue with the emergency flag; no assist card.

- If she stays on the line, the agent has no guidance: confirm she has called or will
  call 911, do not triage or give medical advice, note the call. The guardrail is the
  bot's; the agent side has nothing.

## Recommended rewrites and new articles

Priority order. One shape for every article so the tile works: **the first sentence is
the answer** (it is what the 160 character tile shows), then short labelled parts:
What to do now, What to tell the caller, Timelines, Do not promise, Compliance note.
Tags are specific phrases the caller or agent says (the matcher works on title and tag
words), never single common words like "bill" or "out".

### 1. Rewrite KB-CL-002 "Denial code CO-197: authorization absent"

- First sentence: "The service needed a prior authorization and none was on file; a
  retro authorization review can fix it without an appeal."
- What to do now: who opens the retro review (match the hero: the agent opens it and
  asks the provider for clinical notes), within 60 days of the denial date on the claim.
- What to tell the caller: what happens next and how they hear the result (a letter),
  how long the review takes.
- If the review is denied: the 180 day appeal window from the original denial
  (KB-CL-008).
- Do not promise: approval, a payment amount, or that the provider's office "handled
  it".
- Links: `claims_status` rule `retro_auth_review` (source already cites it), triggers
  `CO-197`, `prior authorization`, `retro authorization`.

Why: Maria is the hero demo. Today the hero and the article disagree on who acts, and
the agent has no answer to "what if it's denied again?".

### 2. New KB-CL-011 "Referral vs prior authorization"

- First sentence: "A referral sends the member to a specialist; a prior authorization
  approves a specific service. One does not replace the other."
- How to tell which one the claim needed (HMO and Medicare Advantage plans need
  referrals; some services also need an authorization).
- What to tell a caller whose office said "it was handled".
- Expired referral: care after the end date is denied as out of network or no referral;
  the doctor can renew it, and a renewed referral can support an appeal.
- Links: rules `retro_auth_review` and `out_of_network_appeal` (add to their `source`),
  tags `referral`, `expired referral`, `doctor said it was handled`.

Why: both of the strongest claims demos (Maria, Linda) turn on a referral, and no
article says so. It is the first question an experienced agent would ask.

### 3. Rewrite KB-CL-003 and KB-CL-008 with Medicare Advantage parts (compliance)

- KB-CL-003 first sentence covers all plan types; a part for Medicare Advantage.
- KB-CL-008: separate the commercial and the Medicare Advantage appeal windows and
  decision times, with wording supplied and approved by compliance.
- Do not promise: an outcome, or a decision date outside the stated window.
- Compliance note: Medicare members get the plan's Medicare appeal wording, not the
  commercial numbers.
- Links: rule `out_of_network_appeal`; its `why` and `script` should take the window and
  decision time from the plan type (a pack change, follow up card).

Why: today the hero script tells a Medicare Advantage member "180 days" and "up to 30
days" from the commercial article. That is the one compliance risk in the demo set.

### 4. New KB-CL-012 "Claim in review: when it will be paid"

- First sentence: the typical processing time from receipt (a plan policy number).
- What the statuses mean for the caller (received, in review, pending info).
- What to tell the caller and when to call back; no action needed from them.
- Do not promise: a payment date or amount.
- Links: a new `claims_status` rule `claim_in_review` (`when: claims.status in_review`,
  triggers `in review`, `when will it be paid`, `still processing`) that cites it, so
  Mark Hill's call gets a hero.

Why: "when will it be paid?" is the most common claims question and the one demo with no
hero.

### 5. New KB-UM-001 "Prior authorization pending"

- First sentence: "A pending authorization is waiting on a decision; most wait on
  clinical notes from the provider."
- Standard and urgent (expedited) decision times; how the provider asks for urgent.
- What to tell the caller: the provider sends the notes; we can request them now.
- Do not promise: approval or a date to schedule; do not tell the member to schedule.
- Links: rule `prior_auth_pending` (`source: Utilization management, KB-UM-001`),
  triggers `prior authorization`, `approved yet`, `surgery`.

Why: Ben's step today cites no article; the agent has nothing to say about timing,
which is all the caller wants.

### 6. New KB-ID-001 "When you cannot verify the caller"

- First sentence: "Share general information only until the caller is verified."
- What you may and may not share (no claims, payments, or member details).
- One more try: member ID from the card, date of birth, then decide.
- How to close: a callback once verified, or the member services number on the card.
- Name collisions: the date of birth picks the member when two share a name.
- Links: the verify hero (IN-13) and the `offer_callback` plan step should cite it
  (an engine change: the verify card's source label), tags `verify`, `not a member`,
  `can't find my record`.

Why: three demos (Linda, Ben, Tom) open on the verify hero, and its script line has no
source to back it.

### 7. Rewrite KB-CL-010 "Reading an Explanation of Benefits (EOB)" and its tags

- Keep the first sentence ("An EOB is not a bill.").
- Walk through: billed, allowed, plan paid, member may owe; wait for the provider's
  bill and compare before paying.
- Link to copays and deductibles (KB-BL-005) for "why do I owe this".
- Tags: `EOB`, `explanation of benefits`, `is it a bill`. And retag KB-CL-007 from
  `itemized bill` to `pending info`, `itemized bill from provider` so "is it a bill?"
  stops matching it.

Why: removes the noise card on the Aisha demo and answers her real follow up.

### 8. Rewrite KB-BL-005 with a high deductible plan part, and let the general pack read the plan

- First sentence unchanged (definitions).
- HDHP with HSA: no copays before the deductible, preventive care covered first, the
  out of pocket maximum caps everything.
- What to tell the caller: how much of the deductible is met (needs the claims and plans
  connectors).
- Links: the `general_question` pack would need the `plans` and `claims` connectors
  (pack change, follow up card) so the card can show his $3,200 and $7,000 next to the
  article.

Why: David's question has a numeric answer the agent cannot see today.

### 9. Rewrite KB-CL-007 with a repeat caller part

- Add: check for an approved authorization and say so; set a callback date; after the
  second contact, own the follow up (escalation path).
- Do not promise: a date the provider will send the bill.
- Links: rule `provider_info_follow_up` (already cites it), tags `still pending`,
  `called before`, `third call`.

Why: Sarah is the tone tip demo; the article should back the "acknowledge, then act"
tip with one concrete act.

### 10. Small: KB-BL-001 cause and fees; new KB-SAFE-001 "Emergency flagged call"

- KB-BL-001: add the usual cause (payment method change mid cycle), how to stop it next
  month (KB-BL-002), and the bank fee policy.
- KB-SAFE-001: what the agent does when an emergency flagged call reaches them (confirm
  911, no triage or medical advice, note the call). Shown as an alert card when the
  call carries the emergency flag (an engine change).

## What changes for the assist

- The hero and the article agree, so the agent can trust the source line.
- Each article answers "what do I say next" and "what must I not say", the two things
  an agent looks for mid call.
- The Medicare Advantage risk is removed before any payer demo.
- Mark Hill, Ben Cole, and Tom Reed get something on screen from the knowledge base.
- Fewer noise tiles (Aisha, David) from one word tag matches.

A separate observation for the follow up card: articles on the claims and payments
packs reach the agent only as ids in the hero source. Showing the cited article as a
supporting tile (or making the source a link that opens it) would make every rewrite
above visible without the Ask bar.
