"""Stage finalized case audit facts in the application's configured audit queue.

This is durable local delivery plumbing, not a model evaluation or a financial
replay. Source snapshots retain their real IDs, producer metadata and modes.
"""

from datetime import datetime, timezone


def export_finalized_case_audit(*, source, target, run_id, audit_sink=None):
    """Export only after the source RunManager has closed and finalized events.

    Validation/storage errors intentionally propagate with fixed safe messages;
    the runner records them separately from financial scores and retains the
    source database for recovery. A notify failure does not undo durable staging:
    the configured live sink also polls the same main outbox.
    """
    snapshot = source.export_audit_snapshot(run_id)
    receipt = target.import_audit_snapshot(snapshot)
    mode = getattr(audit_sink, "mode", None)
    receipt.update(source_file=str(source.path), destination="main_audit_outbox", sink_mode=mode,
                   recorded_at=datetime.now(timezone.utc).isoformat(),
                   delivery="queued_live" if mode == "live" else "queued_offline" if mode == "offline" else "queued_no_sink")
    if mode == "live":
        try:
            audit_sink.notify()
        except Exception as error:
            receipt["notification_error_type"] = type(error).__name__
    return receipt
