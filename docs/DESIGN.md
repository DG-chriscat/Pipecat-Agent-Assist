# DESIGN.md: Deepgram Vitals agent portal design system

Status: locked, Nov 15, 2026 (Chris chose concept 1, "Focus").
Reference build: [design/in-call-focus.html](design/in-call-focus.html) and [design/in-call-focus.png](design/in-call-focus.png). When this file and the mockup disagree, this file wins. Open the HTML in a browser to inspect exact CSS.

## Principles
1. **One answer, not a feed.** The agent sees one next best step at a time, in large type, with the words to say and one action. Everything else is supporting detail.
2. **Calm by default, loud only when it matters.** Fuchsia is reserved for AI suggestions and caller emphasis. Clay red only for alerts and denials.
3. **Show why.** Every AI element names its trigger ("Heard 'prior authorization'") and its source record or article.
4. **Nothing moves under the cursor.** New cards never push content the agent is reading. The hero card swaps in place with a short crossfade.
5. **Keyboard first.** ⌘K opens Ask. Enter accepts the hero action. Esc dismisses.

## Brand (Deepgram 2026 brand kit, mandatory)
Only these fonts, colors, and materials. No other hues, no other fonts.

| Token | Value | Use |
| --- | --- | --- |
| `--font-sans` | Work Sans 400/500/600 | All body and UI text |
| `--font-mono` | Azeret Mono 400/500 | Labels, chips, buttons, IDs, amounts, timers |
| `--fuchsia` | #E042FF | AI suggestions, caller label, trigger underline, hero glow |
| `--led` | #7F39FF | Gradient partner to fuchsia, background glow |
| `--off-black` / `--off-white` | #1D1D1D / #FCFAF8 | Base text and surfaces |
| `--clay` | #EB2A2E | Alerts, denied, end call, live dot |
| `--bakelite` | #FA8100 | Frustration, warnings, repeat caller |
| `--phosphor` | #7CD941 | Verified, paid, completed steps |
| `--glass-blue` | #00B2FF | Record cards, informational |
| `--brass` | #F7F300 | Reserved, not used in v1 |
| `--gray` | #C4C4C4 | Functional only |

Perfektta (headline font) and brand Artifacts are design team only. Do not use them.

Logo: [design/deepgram-d-tile.png](design/deepgram-d-tile.png) (30 px, radius 8) plus [design/deepgram-wordmark-offwhite.png](design/deepgram-wordmark-offwhite.png) (19 px tall), top left. Replace with official SVGs from the brand Dropbox when available.

Copy: sentence case everywhere, no exclamation points, Oxford comma.

## Surfaces and materials
- Page: `#0b0b0c` with two soft radial glows (LED top left at 30%, fuchsia bottom right at 22%) and a paper grain overlay (SVG fractal noise, 45% overlay).
- Glass panel: `rgba(255,255,255,.055)` fill, `1px rgba(255,255,255,.12)` border, `backdrop-filter: blur(24px) saturate(140%)`, radius 22 px.
- Hero card: glass plus fuchsia border `rgba(224,66,255,.45)` and glow `0 30px 80px -30px rgba(224,66,255,.45)`.
- Text: primary #FCFAF8, muted 58%, faint 38%.

## Type scale
| Role | Font | Size / weight |
| --- | --- | --- |
| Hero headline | Work Sans | 30 / 500, tracking -0.02em |
| Caller name | Work Sans | 24 / 500 |
| Card title | Work Sans | 14.5 / 500 |
| Body | Work Sans | 14 / 400 (hero body 15) |
| Label | Azeret Mono | 10.5, uppercase, tracking 0.09em, faint |
| Chip | Azeret Mono | 10, uppercase, pill, tinted bg at 15% of its color |
| Button | Azeret Mono | 11, uppercase, pill |

## Layout (in call, 1440 px and up)
Three columns, 18 px gaps, 22 px page padding:

```
[ top bar: logo | call island | controls                                  ]
[ caller panel 270 | assist column (flex)                | transcript 340 ]
```

### Top bar
- Left: D tile, wordmark, divider, "Vitals" (product name: Deepgram Vitals).
- Center, the **call island** (pill, black glass): verified dot, caller name, Verified chip, live waveform (fuchsia to LED), mood chip, timer.
- Right: mute, hold (40 px round glass), End (clay pill). Positions never change.

### Caller panel (left)
Top to bottom: label, name, member ID and plan (mono), **Why they called** box, key facts list (born, claim, waited, calls in 30 days with Repeat chip), **Before this call** timeline (last 3 events, colored dots), **Mood over call** sparkline pinned to the bottom (phosphor to bakelite to clay gradient, Calm to Upset axis).

### Assist column (center)
1. **Hero: next best step.** Chip "Next best step", trigger line, 30 px headline, one or two sentence why, **Say this** script block (quote icon, 15.5 px), primary action (fuchsia gradient button), secondary "Show me why", source line.
2. Label "Ready when you need it", then a row of three supporting cards: **record** (for example recent claims table), **call plan** (pack checklist with progress, auto ticks), **tone tip**.
3. **Ask bar** pinned to the bottom: spark icon, placeholder, ⌘K hint.

### Transcript (right)
Live chip, lines labeled CALLER (fuchsia) and YOU (muted). Trigger words underlined in fuchsia. Current agent line shows a fuchsia caret.

## Components
| Component | States |
| --- | --- |
| Hero card | empty ("Listening"), suggestion, accepted (phosphor check, collapses to history), dismissed |
| Supporting card | record, call plan, tone tip, article, alert (clay border). Each: pin, dismiss, thumbs up/down, source |
| Call plan step | done (phosphor, struck through), next (fuchsia ring), todo |
| Chip | ok, warn, bad, ai, info, neutral |
| Button | primary fuchsia gradient, secondary outline, white solid |
| Call island | ringing, on call, hold, wrap-up |

Icons: Lucide, 1.8 stroke, round caps (closest match to the brand icon style).

## Motion
- Hero swap: 180 ms crossfade plus 8 px rise. No layout shift.
- Supporting cards: fade in at their slot, never reorder while hovered.
- Waveform bars animate only while someone is speaking.
- Respect `prefers-reduced-motion`.

## Out of scope for this file
Sign in, queue, and incoming call screens follow the same tokens; layouts get added here before their cards are built.

## Wrap up (P3-5)
After End or a caller hang up, the in-call frame closes and the center stage shows one glass card (max 600 px), same tokens as the incoming call card:
- Head: info chip "Wrap up" (clipboard check icon) and a faint label naming who ended it ("You ended the call" or "The caller hung up").
- Caller name (24 / 500), Verified chip, call length chip (mono `mm:ss`), then the **Why they called** box.
- **Summary draft** box: AI chip "Summary draft". Until AP-9 lands it is a dashed fuchsia outline with faint placeholder copy and a "Coming soon" label; with a draft it is a solid hero-line border.
- **Reason code**: label, then the active pack's codes as mono pill buttons (radio group). Selected: fuchsia border and 16% fuchsia fill.
- **Notes**: textarea in the sign in field style. Cmd or Ctrl plus Enter saves.
- Primary button "Save and go available" (fuchsia gradient), disabled until a reason code is picked.
- Call island in wrap-up state: glass-blue dot, caller name, info chip "Wrap up", queue count.
- After Save the idle home returns with the notice "Call saved. You are available."
