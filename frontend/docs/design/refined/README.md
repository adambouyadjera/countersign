# Countersign design reference v2: "Refined Paper & cobalt" (APPROVED)

Supersedes the v1 mockups in frontend/docs/design/. Same palette, same Sig, same layouts;
new fonts and a stronger paper-and-ink box style. Reference only: do not import or run.

| File | Screen |
| --- | --- |
| RefinedConsole.dc.html | Console (palette: the `paper` entry of `palettes`) |
| RefinedCaseFile.dc.html | Case file |
| RefinedAttackLab.dc.html | Attack Lab, "red team mode" (dark, hazard stripe) |
| RefinedBooks.dc.html | Books |
| RefinedEval.dc.html | Proof |
| RefinedAudit.dc.html | Audit |

## Type
- Display: Bricolage Grotesque 800 (page titles, Sig's name, big numbers in Attack Lab), letter-spacing -0.015em.
- Body: Instrument Sans 400/500/600/700.
- Mono: Space Mono 400/700 (labels, numbers, IDs, handles, nav, Sig's face).

## Surfaces (the important part)
- Page ground: #F6F4EF with a dot grid: radial-gradient(#DED7C8 1px, transparent 1.3px) 0 0 / 18px 18px.
- Card: white with a folded corner (linear-gradient(225deg, #F6F4EF 0 12px, #D3CDC1 12px 13px, #FFFFFF 13px)),
  border 1.5px solid #1A1C22, radius 8px, box-shadow 3px 3px 0 #E2DCCF.
- Card label: a dark tab: Space Mono 10.5px, letter-spacing .06em, #F6F4EF on #1A1C22, padding 2px 8px, radius 3px.
- Untrusted/danger content: 1.5px DASHED #B42318 border on #FCE9E6 (dashed = untrusted, everywhere).
- Inner items (rows, journal entries, check rows, inputs, chips, secondary buttons):
  1.5px solid #1A1C22, radius 4-6px, box-shadow 2px 2px 0 #E2DCCF. Table row dividers: 1px dashed #C9C2B3.
- Primary button: cobalt #2448C8, white text, 1.5px solid #1A1C22, box-shadow 3px 3px 0 #1A1C22.
- Header: 2px solid #1A1C22 rule underneath.
- Sig speech bubble: #EEF2FD, 1.5px solid #2448C8, radius 16px 16px 16px 3px, box-shadow 3px 3px 0 #C9D5F7.

## Attack Lab "red team mode" (only this page)
- Ground #15161B with red dot grid (#3A1E1E), 12px hazard stripe on top
  (repeating-linear-gradient(-45deg, #FF5A4E 0 14px, #15161B 14px 28px)), header rule #FF5A4E.
- Panels #1F2129, 1.5px solid #FF5A4E, box-shadow 4px 4px 0 #7A1D17; tabs red #FF5A4E with dark text.
- Text #F3EEE7, muted #A9A3B0, danger text #FF8A80. Textarea #0F1014 with dashed red border.
- Launch button: #FF5A4E, dark text, 2px #F3EEE7 border, box-shadow 5px 5px 0 #F3EEE7, Bricolage 20px.
- Sig stays calm: the normal cobalt bubble. "What a human sees" stays a white paper card.
