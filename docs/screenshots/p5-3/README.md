# P5-3: visual polish pass against the Studio mockups

Each sheet is BEFORE (main at 541022b) on the left and AFTER (this branch) on the right,
1600 x 1000 unless the name says 1440 (laptop, 1440 x 900) or 1920 (projector,
1920 x 1080). Light and dark for every screen. The screens come from the dev screen
gallery (`portal/src/dev/screens.tsx`, mock data) so each state can be shot on its own;
`portal/scripts/shoot_screens.py` takes them and `pair_sheets.py` builds the sheets.
Reference: `docs/design/studio/in-call-studio-light.png` and `-dark.png`, DESIGN.md.

| # | Screen | What changed |
| --- | --- | --- |
| 01 | Sign in | Foot note ("Demo sign in ...") moves from faint (2.7:1) to muted ink (4.7:1). |
| 02 | Home, idle | Faint helper text to muted ink; visible focus ring on every control. |
| 03 | Incoming call | Neutral chip ink ("Waited 7 s") darkened from 4.46:1 to 5.4:1 in light. Enter on a focused Decline button presses Decline instead of accepting the call. |
| 04 | In call, listening | The call grid sat in an auto row when there was no error line, so it stopped short of the bottom whenever the columns were short (see the empty and listening states); it now fills to the bottom like the mockup (1600 x 1000: top 88, bottom 974 px), with the Ask bar pinned there. Plan and masked member ID sit on two lines instead of wrapping with a dangling "·". |
| 05 | In call, suggestion | Same grid fix. Tile answers scale with the tile and wrap at words ("Acknowledge, then act" no longer breaks mid word). Call plan count sits beside its progress bar so the "Call plan" title never truncates. Plan list fades at the cut instead of slicing a line. Hero action bar fades in over a scrolled script instead of a hard band. Source, tile source, tile tools, timer, and history time stamps move from faint to muted ink. |
| 06 | In call, accepted | Grid fix; "Earlier this call" time stamps at AA. |
| 07 | Wrap up | Empty summary draft copy at AA. The center stage scrolls from the top when the card is taller than the screen (wrap up at 1440 x 900) instead of clipping the head. |
| 08 | In call, connecting (no data yet) | Conversation column says "Connecting" (not "Live") and names the call audio until the room joins. |
| 09 | In call, LLM error | New: the bot sends `error` then `listening` at once, so the portal showed nothing. The hero now keeps a warn note, "Suggestions are paused: the assist model is not answering. Records and the call plan still update.", until the next suggestion; the orb rests. Same for `stopped`. |
| 10 | In call, slow connector | New: after 4 s without an answer the Ask note reads "Still looking up ... The records system is slow." |
| 11 | Agent drop (QH-9) | The drop, missed, and declined notices are a warn callout with an icon instead of plain gray body text; "Call saved" is an ok callout. |
| 12 | In call, audio error | The call audio error is a clay pill above the grid instead of loose red text; the grid still fills the rest. |
| 13 | Wrap up, load error | Error and Try again unchanged; faint copy fixed. |
| 14 | Home, reconnecting | Unchanged layout; faint copy fixed. |
| 15 | In call, suggestion, 1440 x 900 | Grid fills the laptop screen. Hero actions stay on one row (key hints hide below 760 px of assist width; the keys still work) and the source drops under them. |
| 16 | Wrap up, 1440 x 900 | Card scrolls inside the stage instead of clipping. |
| 17 | In call, suggestion, 1920 x 1080 | Projector: side columns widen (340 and 400 px), transcript text 14.5 px, hero headline line length capped. |
| 18 | Live call vs the mockup | A real call (`server/tests/test_studio_screens_live.py`, Daily + Deepgram + Anthropic) after this change, next to `in-call-studio-*.png`. Not a before/after: the reference is on the left. |

Keyboard and motion (no screenshot):
- Cmd+K (Ctrl+K off Mac) focuses Ask; the hint shows "Ctrl K" off Mac.
- Enter accepts the hero or the incoming call only when focus is not on another button;
  Esc dismisses the hero, declines the incoming call, and leaves the Ask field.
- Tab order: rail, header controls (mute, hold, End), caller panel (Reveal), assist
  column (hero actions, tiles, Ask), conversation.
- Every focusable control gets a 2 px fuchsia focus ring (`:where(...):focus-visible`
  in tokens.css; component rules keep their own offsets).
- `prefers-reduced-motion`: animations and transitions already stop; smooth scrolling
  now stops too.

Contrast (WCAG AA 4.5:1 for body text, both themes, composited over the glass; checked
by `portal/src/styles/contrast.test.ts`): text and muted ink on panel and hero glass,
every chip tone (ok, warn, bad, ai, info, neutral) on panel and hero. Light neutral chip went from 4.46 (panel) and 4.49 (hero) to 5.38 and 5.42 with a new
`--neutral-ink`. Faint ink (2.7:1 in light) stays only
on the 10 px mono labels DESIGN.md calls faint.
