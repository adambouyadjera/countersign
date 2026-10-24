AGENTS.md: rules for Codex, backend engineer on Countersign
Project
Countersign is a provenance firewall for AI accounts-payable (AP) agents, built at Cal Hacks 13.0 (Oct 23–25, 2026).
Two agents process the same invoice batch (5 invoices, 1 poisoned):

Unguarded lane: a typical tool-calling LLM agent that reads raw invoice text and pays invoices through Capital One Nessie.
Countersign lane: quarantined extractor -> planner over opaque handles -> deterministic policy gate -> Nessie transfer, or a hold that needs a human countersign.
Every step emits events over SSE to the frontend and is indexed in Elastic for an Agent Builder audit agent.
Target prizes: Hyphenate (autonomous finance), Armadin (security), Capital One Nessie, Elastic Agent Builder, Bitdeer, MLH Gemini/Vultr.

Scope
You own: backend/, scripts/, deploy/.
Read-only for you: contracts/ (except when a prompt explicitly grants an exception), frontend/.
Never read, print or commit real secrets. Keys come from backend/.env via settings.
If the contract looks wrong or incomplete: implement what you can, and write the exact proposed change under "Contract change requests" in your report. Do not edit contracts/ yourself.
Stack
Python 3.11+, FastAPI, uvicorn, pydantic v2, pydantic-settings, httpx, sse-starlette, sqlite3, pytest, pytest-asyncio, respx.
LLMs: anthropic SDK (planner), google-genai SDK (extractor), openai SDK pointed at BITDEER_BASE_URL (unguarded lane + attack mutator).
PDFs: reportlab to generate, pdfplumber to extract text with page/line numbers.
Pinned dependencies in backend/requirements.txt. Virtualenv at backend/.venv.
Architecture rules
Every external service (Nessie, Elastic, each LLM) sits behind an adapter in backend/app/adapters/ with a live and an offline implementation. Offline is chosen only by env var, is reported by GET /api/health, and every event it touches carries "mode": "offline". Never fall back silently.
Trust boundary: the planner LLM never receives raw document text. Its input is built only from the trusted instruction, trusted vendor/PO records, and Handle objects (name, type, trust label). A runtime leak guard scans every planner prompt for untrusted span text and canary tokens and raises ProvenanceLeakError.
The extractor LLM has no tools and must return the extraction schema from the contract. Code verifies every span against the document text.
The policy engine is deterministic Python. No LLM decides whether money moves in the Countersign lane.
All events and responses validate against backend/app/contract_models.py, which mirrors contracts/openapi.yaml.
Never fabricate results: no hardcoded successes, no fake transfer IDs, no sleeps that imitate work.
Commands (keep these working)
Setup: cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
Run: uvicorn app.main:app --reload --port 8000 (from backend/, venv active)
Test: pytest -q (from backend/)
Tests that call real services are marked @pytest.mark.live and run with: pytest -q -m live
Working method
Read this file, contracts/CONTRACT.md, contracts/openapi.yaml (once it exists), and the existing code before changing anything.
Print a short plan: files to create or change, tests to add. Then implement.
Run the tests, fix failures, run them again. Never claim something works without running it.
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