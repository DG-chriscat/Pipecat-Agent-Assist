# ADR 0001: Agent portal layout is "Focus" (one next best step)

Date: Nov 15, 2026 · Decided by: Chris Catalano · Recorded by: Dutch

## Context
Three in-call concepts were mocked with the Deepgram 2026 brand kit: Focus (one hero suggestion), Timeline (cards inline in the transcript), Ambient (now, next, know columns). References came from Screenrove (Sentry, Grammarly, Twenty, Slack).

## Decision
Use Focus. The assist shows one next best step at a time with a script and one action, supported by record, call plan, and tone cards, a caller panel with history and mood trend, and a live transcript on the right.

## Consequences
- The PRD gains a Next best step requirement, a call plan, caller history, and a mood trend (AP-11 to AP-15, DC-8 to DC-10).
- The mock database needs prior interactions, referrals, and authorizations, not only members, claims, payments, and articles.
- The assist must rank suggestions and pick one, not just emit cards.
- DESIGN.md is the build contract for every portal card.
