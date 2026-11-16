# DESIGN.md: Deepgram Vitals agent portal design system

Status: locked, Nov 16, 2026. Chris chose **Studio** (variant C2 of the Care direction), light and dark themes. This supersedes the Nov 15 dark Focus styling (ADR 0005). The Focus layout principles (one next best step, show why, keyboard first) carry over.

Reference builds, open in a browser to inspect exact CSS:
- Light (default): [design/studio/in-call-studio-light.html](design/studio/in-call-studio-light.html), [PNG](design/studio/in-call-studio-light.png)
- Dark (vinyl): [design/studio/in-call-studio-dark.html](design/studio/in-call-studio-dark.html), [PNG](design/studio/in-call-studio-dark.png)

When this file and the mockups disagree, this file wins. Earlier explorations live in [design/explorations/](design/explorations/) for history only.

## Principles
1. **One answer, not a feed.** One next best step at a time, in large type, with the words to say and one action. Supporting cards lead with one big answer, not a table.
2. **Calm by default, loud only when it matters.** Fuchsia is for AI suggestions and trigger words. Clay red only for alerts, denials, and End.
3. **Show why.** Every AI element names its trigger ("Heard 'prior authorization'") and its source record or article.
4. **Nothing moves under the cursor.** New cards never push content the agent is reading. The hero swaps in place with a short crossfade.
5. **Keyboard first.** Cmd+K opens Ask. Enter accepts the hero action. Esc dismisses.
6. **Privacy is visible.** PHI is masked by default, identity status is always on screen, and revealing data is a deliberate click.
7. **No black boxes.** Surfaces are glass over material. Nothing is a solid black slab in light mode.
8. **Progress is felt.** "Earlier this call" shows what the agent already did, so the screen rewards the work.

## Brand (Deepgram 2026 brand kit, mandatory)
Only these fonts, colors, and materials. No other hues, no other fonts.

| Token | Value | Use |
| --- | --- | --- |
| `--font-sans` | Work Sans 300/400/500/600 | All body and UI text |
| `--font-mono` | Azeret Mono 400/500 | Labels, chips, buttons, IDs, amounts, timers |
| `--fuchsia` | #E042FF | AI suggestions, trigger highlight, hero glow, primary button |
| `--led` | #7F39FF | Gradient partner to fuchsia, aurora, agent avatar tint |
| `--off-black` / `--off-white` | #1D1D1D / #FCFAF8 | Text and base surfaces |
| `--clay` | #EB2A2E | Alerts, denied, End, live dot |
| `--bakelite` | #FA8100 | Worry and frustration, warnings, repeat caller |
| `--phosphor` | #7CD941 | Verified, paid, completed, identity ring |
| `--glass-blue` | #00B2FF | Informational, Reveal links |
| `--brass` | #F7F300 | Reserved, not used in v1 |

Perfektta (headline font) and brand Artifacts are design team only. Do not use them.

Logo: [design/deepgram-d-tile.png](design/deepgram-d-tile.png) in the left rail (36 px, radius 10) and the wordmark in the header (16 px tall; dark ink in light mode, off white in dark mode). Replace with official SVGs from the brand folder when available.

Copy: sentence case everywhere, no exclamation points, Oxford comma.

## Themes and materials
Two themes, same layout and components. Light is the default. The agent switches in the rail menu; the choice is saved per agent (local storage is fine for the PoC). Respect `prefers-color-scheme` on first visit.

Material textures are cropped from the brand deck material palette (slide 14) and live in [design/studio/tex/](design/studio/tex/). They are PoC assets; request official files through Brand Central before any external use. Textures never sit directly under body text: always behind a glass layer of at least 50% opacity or inside a small swatch.

| Layer | Light | Dark |
| --- | --- | --- |
| Ground | Paper tile under a 88 to 92% off white wash | Vinyl tile under a 55 to 85% near black wash (#0e0d10), darker toward the bottom |
| Horizon (bottom third) | Stone tile, 55% | Vinyl at larger scale, brightness 0.7 |
| Aurora | One blurred orb (fuchsia, LED, glass blue) behind the hero only, 55% | Same orb, 42% |
| Panel | `rgba(255,255,255,.58)`, blur 36 px, saturate 170%, 1 px white border, radius 30 | `rgba(30,28,34,.52)`, same blur, 1 px `rgba(255,255,255,.08)` border |
| Hero | `rgba(255,255,255,.72)`, blur 40 px, radius 36, LED shadow | `rgba(34,31,38,.60)`, fuchsia ring 18% and fuchsia shadow |
| Call island | Frosted white glass pill | `rgba(255,255,255,.08)` glass pill |
| Text | #1D1D1D, muted 62%, faint 44% | #FCFAF8, muted 66%, faint 44% |

Card swatches (34 px, radius 11, icon on top): Aluminum = records and claims, Stone = call plan and articles, Bakelite = tone and mood, Copper = the caller (avatar ring fill), Clay = alerts.

Chip colors are tuned per theme for contrast (see the mockup CSS variables `--ok`, `--warn`, `--bad`, `--ai`, `--info` and their `bg` pairs). Body text must meet WCAG AA in both themes.

## Type scale
| Role | Font | Size / weight |
| --- | --- | --- |
| Hero headline | Work Sans | 40 / 500, tracking -0.03em, ends with a period |
| Caller name | Work Sans | 24 / 500, may wrap to two lines |
| Tile answer | Work Sans | 26 / 500 |
| Card title | Work Sans | 14 / 500 |
| Body | Work Sans | 14 (hero body 16, script 17, caller quote 17) |
| Label | Azeret Mono | 10, uppercase, tracking 0.1em, faint |
| Chip | Azeret Mono | 9.5, uppercase, pill, tinted bg |
| Button | Azeret Mono | 11, uppercase, pill |

## Layout (in call, 1440 px and up; mockups at 1600 x 1000)
```
[rail 84 | header: wordmark | Vitals | PHI masked | call island (centered) | mute hold End          ]
[        | caller 310        | assist column (flex)                        | conversation 340      ]
```
Gaps 22 px, stage padding 22 / 30 / 26 px.

### Left rail
D tile on top, then icons: Calls (active), Queue, History. Agent avatar at the bottom with a phosphor available dot. Queue and History may be inactive in the PoC. The theme switch lives in the avatar menu.

### Header
Wordmark, divider, "Vitals", **PHI masked** chip (lock icon). Centered **call island**: phosphor dot, caller name, Verified chip, live waveform (fuchsia to LED), timer. Right: mute and hold (40 px round glass), End (clay pill). Positions never change.

### Caller panel (left)
1. Portrait: copper avatar with initials inside a phosphor ring once identity is confirmed (no ring before). Name, plan, masked member ID (`••••10`) with a **Reveal** link.
2. Badges: **Identity 3 of 3** (shield icon; shows which checks passed on hover), Repeat caller when calls in 30 days is 2 or more.
3. **In her words**: the caller's reason as a short quote, from intake.
4. Settings style rows: claim, waited, referral, last call (from intake and caller history).
5. **Mood** pinned to the bottom: label chip ("Worried, easing") plus a bar chart of tone per caller turn (fuchsia to LED for calm, bakelite for worried). Tooltip: "Tone reader, words only."

### Assist column (center)
1. **Hero: next best step.** Chip "Next best step", trigger line, 40 px headline, one or two sentence why, **Say this** block (fuchsia to LED bar on the left), primary action (fuchsia gradient, Enter), "Show me why", "Not now" (Esc), source line.
2. **Shelf** of three tiles, each with a material swatch, a title, one big answer, and one line of context: record (for example "$1,240", denied code), call plan ("Open request", 2 of 4), tone tip ("Lead with cost").
3. **Earlier this call**: checked rows with a time stamp for accepted actions and plan steps done.
4. **Ask bar** pinned to the bottom: spark icon, "Ask about Maria", Cmd+K hint.

### Conversation (right)
"Conversation" label and Live chip. Each line: 26 px avatar (caller copper gradient with initials, agent LED tint with "You"), then text. Agent lines muted. Trigger words get a fuchsia highlighter (bottom 40% of the line). The line still being spoken shows a fuchsia caret.

## Components
| Component | States |
| --- | --- |
| Hero | listening ("Listening with you"), suggestion, accepted (phosphor check, moves to Earlier this call), dismissed |
| Shelf tile | record, call plan, tone tip, article, alert (clay swatch). Each: pin, dismiss, thumbs up/down, source on hover |
| Call plan step | done (phosphor, struck through), next (fuchsia ring), todo |
| Identity | not checked, partial (n of 3, bakelite), confirmed (3 of 3, phosphor ring), verified by agent (phosphor ring, agent name on hover), not verified by agent (clay) |
| Verify hero (IN-13) | first hero for a caller who is not verified: "Verify caller" chip (bakelite; clay when not found), headline "Confirm who you're speaking with", one row per check (phosphor check or bakelite cross; heard vs on file for the name, never the DOB), **Say this** script, **Mark verified** (primary, Enter), **Could not verify**; no Not now / Esc. Ack: "Caller verified" (phosphor shield) or "Identity not confirmed" (muted), then a row in Earlier this call |
| Locked tile (IN-13) | stone swatch with a lock, "Locked record", "Verify the caller to see records". No values, no source, no tools. Replaced by the real tile on Mark verified |
| PHI field | masked (default), revealed (logs a reveal event on the call record, re-masks after 30 s), disabled (muted Reveal, "Verify the caller to reveal", until the caller is verified) |
| Chip | ok, warn, bad, ai, info, neutral |
| Button | primary fuchsia gradient, secondary soft fill, End clay |
| Call island | ringing, on call, hold, wrap up |

Icons: Lucide, 1.7 stroke, round caps.

## Motion
- Hero swap: 180 ms crossfade plus 8 px rise. No layout shift.
- Tiles fade in at their slot and never reorder while hovered.
- Waveform bars animate only while someone is speaking.
- Theme switch: 200 ms color crossfade, no layout change.
- Respect `prefers-reduced-motion`.

## Other screens
Sign in, idle home, and the incoming call card use the same themes, ground, rail, and glass. Their current builds (P3-1, P3-2) are restyled to this file by the Studio restyle card.

## Architecture (P5-6, AP-21)
Reference mockups: docs/design/architecture/ (business light, technical dark, walk light). Rail icon (Lucide Network) between History and Settings; route /agent/architecture. Content lives in one file, portal/src/architecture/content.ts; every measured number names its source doc and a test checks the doc contains it.
- Header right: Business / Technical segmented control (selected uses the AI gradient), Walk a call, Export, Present.
- Canvas: glass panel with the 1100 x 620 map scaled to fit. Nodes are glass tiles with a material or gradient swatch (glass blue for Deepgram, AI gradient for the assist). The bot's parts sit in a dashed group. Edges are dashed; their protocol labels show only in Technical.
- Selected node: 2 px fuchsia outline. Walk: active nodes get a fuchsia glow and active edges turn solid fuchsia; the rest dim to 32%.
- Side panel (390 px): node details (what, why, measured stat tiles, doc links), the walk step, or with nothing selected the sections as tabs (Data and privacy, Prospect questions, PoC vs production).
- Present: Fullscreen API, no rail or header, the tools move next to the title. Every external link opens in a new tab through ExternalLink.

## Demos (P5-10)
Rail icon (Lucide Play) above Architecture; route /agent/demos. A page title with a **Phone line** glass box on the right (mono number, the password hint). Below: feature filter chips (mono pills, `segment` style, AI tint when on) and a pack segmented control. Cards are glass panels (radius 26) in a grid, min 360 px: name (22 / 500), DOB and plan line, the member ID in groups in Azeret Mono 30 / 500, the case, Pack / To start, say / The audience sees as label plus value rows, feature chips (ok for verified, warn for identity checks and repeat caller, bad for alerts, not found, and the emergency, info for KB, AI for tone tip and wrap up), then Copy details, Start browser call (primary, opens a new tab), Show script. The hero demo card gets the AI chip "Hero demo" and a fuchsia ring. The emergency card shows a clay warning note. The script expands in place as a numbered list on the script background; agent lines carry an AI tinted label.

## Wrap up (P3-5, P5-1)
Layout and content only; P3-5 shipped it in the Focus styling and the Studio restyle card (P4-0) restyles it with the screens above. After End or a caller hang up, the in-call frame closes and the center stage shows one glass card (max 600 px):
- Head: info chip "Wrap up" and a faint label naming who ended it ("You ended the call" or "The caller hung up").
- Caller name, identity chip, call length chip (mono `mm:ss`), then the **Why they called** box.
- **Summary draft** box (P5-1, AP-9): AI chip "Summary draft" and the summary editor inside the box (no inner border; the box takes the focus ring). While the assist is writing it the outline is dashed with a pulsing "Drafting from the call" label; with a draft it is a solid outline with "Edit before you save"; with no draft the outline is dashed and the editor is empty with a "Write a short summary of the call." placeholder.
- **Reason code**: label, then the pack's codes (`wrap_up.reason_codes`) as mono pill buttons in a radio group. Selected uses the AI tint. The assist's suggestion is preselected and carries a small spark plus "Suggested" tag, which stays on that code if the agent picks another.
- **Notes**: textarea (2 rows), "Anything else for the record". Cmd or Ctrl plus Enter saves from the summary or the notes.
- Primary button "Save and go available", disabled until a reason code is picked (the suggestion counts).
- Call island, wrap up state: info dot, caller name, "Wrap up" chip, queue count.
- After Save the idle home returns with the notice "Call saved. You are available."
