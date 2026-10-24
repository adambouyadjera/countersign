# Countersign design reference (approved mockups)

Static mockups from the design canvas. They are REFERENCE ONLY: do not import or run them.
Match their layout, palette, type, spacing and copy closely; replace every sample value and
every [__] / [RUN ID] / [MODEL] placeholder with real data from the API.

Palette: "paper" (Paper & cobalt). In Combined.dc.html the colours come from the `paper`
entry of the `palettes` object in the script at the bottom; use only that palette.

| File | Screen | Route |
| --- | --- | --- |
| Combined.dc.html | Console (inbox, Sig's duel, one sentence two agents, approval card) | / |
| CaseFile.dc.html | Case file for one invoice | /runs/:runId/invoices/:invoiceId |
| AttackLab.dc.html | Attack Lab | /attack |
| Books.dc.html | Books and controls | /books |
| Eval.dc.html | Proof: planted-errors eval + red team | /proof |
| Audit.dc.html | Audit (Elastic Agent Builder) | /audit |

Fonts: JetBrains Mono (UI chrome, numbers, handles) and Manrope (prose, headings).

Sig, the mascot and logo:
- Logo: the small line-art SVG in every header (rounded head, two eyes, two feet) + "countersign".
- Face: three-line terminal glyph. Expressions: default ◉ ◉, serious ▬ ▬ (held / case file),
  cheeky ◕ ◕ (Attack Lab), pleased ^ ^ (books balanced), honest • • (proof), calm - - (audit).
- Sig speaks in short first-person lines in a card next to its face.
