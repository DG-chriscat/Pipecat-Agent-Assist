# ADR 0005: Portal styling is "Studio", light and dark

Date: Nov 16, 2026 · Decided by: Chris Catalano · Recorded by: Dutch

## Context
ADR 0001 locked the Focus layout in a dark theme. Chris asked to lean on the Deepgram brand material palette (Paper, Clay, Stone, Bakelite, Vinyl, Copper, Aluminum, Glass) and to make the portal feel like something Apple would ship and agents want to use. Explorations (in docs/design/explorations/): A subtle dark, B bold dark, C care (light, healthcare), then C1 Daylight and C2 Studio. Chris picked C2 Studio, asked to remove all black boxes, and asked for a dark mode on vinyl with the exact same layout.

## Decision
- Styling is Studio (docs/DESIGN.md): paper and stone ground with one aurora behind the hero, frosted glass panels, a left rail, one big answer per tile, a quote style caller reason, Settings style rows, avatar conversation.
- Two themes, same layout: light (default) and dark on vinyl. Agent chooses; choice is saved.
- Healthcare trust cues become requirements: PHI masked chip, masked member ID and date of birth with Reveal, identity 3 of 3 display, "Earlier this call" progress strip.
- Material textures are cropped from the brand deck for the PoC; official files come from Brand Central before external use. Artifacts stay design team only.

## Consequences
- The Focus layout principles carry over; component placement changes (rail, tiles, conversation avatars).
- Phase 3 screens (sign in, home, incoming call, in call shell, caller panel, transcript) need a restyle card. P4-4 (assist UI) and P4-5 (tone UI) build directly to Studio.
- PRD gains AP-16 to AP-19.
