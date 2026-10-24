CLAUDE.md: rules for Claude Code, frontend engineer on Countersign
Project
Countersign is a provenance firewall for AI accounts-payable agents, built at Cal Hacks 13.0 (Oct 23–25, 2026).
The UI shows two agents processing the same invoice batch side by side: the unguarded agent pays the attacker; the Countersign agent pays the clean invoices and holds the poisoned one. A held payment opens a "case file" with a provenance graph that traces every money-moving value back to either a trusted record or a highlighted span of an untrusted document. Judges can write their own attack in the Attack Lab.

Scope
You own frontend/ only. Do not edit backend/, contracts/, scripts/ or deploy/.
If the contract is wrong or missing something: build what you can, and write the exact proposed change under "Contract change requests" in your report.
Stack
Vite + React 18 + TypeScript (strict). Tailwind CSS. Framer Motion. @xyflow/react with elkjs for graph layout. TanStack Query for REST. A small EventSource hook for SSE. Zustand for UI state. React Router.
Types are generated from contracts/openapi.yaml with openapi-typescript into frontend/src/api/schema.d.ts (npm run gen:api). Never hand-edit generated types.
Tests: Vitest + React Testing Library; Playwright for end-to-end smoke tests.
The dev server proxies /api to http://localhost:8000 (vite.config.ts). VITE_API_BASE_URL can override this in production.
Data honesty rules
Mock mode (VITE_USE_MOCK=1) replays contracts/fixtures/ for development only and shows a visible MOCK DATA badge on every screen.
With mock mode off, every number, status, transfer ID and graph node comes from the backend. No hardcoded results, no fake progress bars, no timers that pretend work is happening.
If /api/health reports a service in offline mode, the UI labels it everywhere that service's data appears.
Design direction: "Refined Paper & cobalt" with Sig
The source of truth is frontend/docs/design/refined/ (read its README first); it supersedes frontend/docs/design/*.dc.html.

Fonts: Bricolage Grotesque (display), Instrument Sans (body), Space Mono (labels, numbers, IDs, handles, Sig's face).
Surfaces: ink-outlined paper cards (1.5px #1A1C22, folded corner, 3px offset shadow), dark label tabs, dashed red for anything untrusted, Sig speech bubbles in cobalt. No plain grey 1px boxes anywhere.
The Attack Lab is the only dark page: "red team mode".
Use the shared primitives in src/components/ui/ (Card, CardLabel, InkButton, Chip, SigSays); never hand-roll a box.

Motifs: hairline ledger rules; handles like $INV-003.remit_account rendered as mono chips; untrusted data drawn with dashed vermilion "taint" edges, trusted with solid ink; held payments stamped with an amber seal.
Motion is meaningful: values flow along graph edges, a stamp lands when a hold event arrives, the attacker balance ticks only when a real transfer event arrives. Respect prefers-reduced-motion.
No generic dashboard template, no default component-library look, no purple gradients, no emoji.
Must read clearly on a projector at 1280x720 and on a 1920x1080 screen.
Commands (keep these working)
npm install | npm run dev | npm run build | npm run test | npm run test:e2e | npm run gen:api | npm run lint | npm run typecheck

Working method
Read this file, contracts/CONTRACT.md, contracts/openapi.yaml and the existing code before changing anything.
Print a short plan: files to create or change, tests to add. Then implement.
Run typecheck, lint and tests; fix failures; run again. Never claim something works without running it. For visual work, run the dev server and check the page in a browser.
Keep changes focused. Do not refactor unrelated code.
Report format (end every task with exactly these headings)
Files changed
Why
Tests executed (exact commands)
Test results (real output: summary lines plus any failures)
Known limitations
How to run this feature
Remaining integration work
Contract change requests (or "none")