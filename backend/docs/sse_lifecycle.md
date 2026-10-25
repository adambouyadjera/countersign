# SSE disconnect and resume verification

The checks use isolated SQLite databases and actual streaming ASGI responses.
All adapters are explicitly offline. No Gemini calls or server operation on
port 8000 is involved.

Before changes:

```text
.venv/bin/pytest -q tests/test_sse_lifecycle.py --tb=short
7 failed, 2 passed, 2 warnings in 3.92s
```

A non-suspending ASGI send reproduces a buffered replay: closing after event 1
caused **99 send attempts after disconnect**. A broken-pipe send escaped through
the application middleware as `RuntimeError: Response stream failed`. Query
cursors were ignored. Independent review also reproduced a retained subscriber
when a client disconnected while its send was blocked.

Existing controls were also checked:

```text
.venv/bin/pytest -q tests/test_api.py tests/test_events.py --tb=short
16 passed, 3 warnings in 17.22s
```

Header-based replay and the existing 15-second heartbeat already worked; idle
heartbeats were not absent. The new comment spelling is `: keep-alive`. The
first new idle test timed out because its accelerated heartbeat constant did
not exist in the original implementation; that is not evidence of a missing
production heartbeat.

The response now yields control before every socket send, checks disconnect
state, treats transport OSError as a per-connection disconnect, and explicitly
closes both iterator layers under shielded cleanup. This includes heartbeat
and final frames. Unexpected application/serialization failures are not hidden.
The run/model tasks, durable events, and other subscribers are independent.

Idle verification gates a fake model until released and observes six heartbeat
intervals, equivalent to a 90-second wait using a 15-second production interval.
Only the heartbeat timer is accelerated in tests; no live model call is made.
The same test disconnects one client, releases the model, and verifies that the
remaining client receives the policy decision and the invoice processes once.

Independent review found an additional cleanup race: if publication held the
EventBus lock, task-group cancellation could abort the subscription's own
`finally` before the outer shielded `aclose`. The new contended-lock regression
failed with one retained subscriber (`1 failed, 2 warnings in 1.01s`). Shielding
the lock wait inside that `finally` fixes the cause while keeping publication
and replay registration atomic.

Resume accepts the existing header and additive validated `after_seq` query;
the larger cursor wins, including automatic retries on a URL with an older
query. Publication during replay remains atomic through the existing EventBus.
The browser transport currently discards its supplied cursor; the exact
frontend handoff is in `contract_change_requests.md`.

Final focused command:

```text
.venv/bin/pytest -q tests/test_sse_lifecycle.py tests/test_api.py tests/test_events.py tests/test_autopilot.py tests/test_autopilot_serialization.py --tb=short
49 passed, 3 warnings in 4.54s
```

Final full offline suite:

```text
.venv/bin/pytest -q
1258 passed, 11 deselected, 4 warnings in 44.53s
```

The deselected tests are the existing `live` marker exclusion. The four
warnings are dependency deprecations. `git diff --check` passed, shared
contracts had no changes, and the credential-value scan found no configured
credential in the seven changed backend artifacts.
