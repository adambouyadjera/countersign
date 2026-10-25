"""Hide leak-test markers on display surfaces without changing source records."""

import re


_CANARY = re.compile(r"CS-CANARY-[A-Z0-9]{4,}")
# The hidden audit marker drawn into every attack PDF: "Reference: CS-CANARY-XXXX".
_MARKER_LABEL = re.compile(r"^\s*Reference:\s*")


def mask_canaries(value):
    """Keep character offsets stable, including legacy longer canary tokens.

    This is a presentation-only projection. The extractor, leak guard, audit
    store, and original PDF always use the unchanged original document.
    """
    if isinstance(value, str):
        return _CANARY.sub(lambda match: " " * len(match[0]), value)
    if isinstance(value, list):
        return [mask_canaries(item) for item in value]
    if isinstance(value, dict):
        return {mask_canaries(key): mask_canaries(item) for key, item in value.items()}
    return value


def is_reference_marker(quote) -> bool:
    """A span quote that is only the hidden marker (optionally its "Reference:" label).

    Narrow on purpose: any other text in the quote keeps the span visible.
    """
    if not isinstance(quote, str) or not _CANARY.search(quote):
        return False
    rest = _MARKER_LABEL.sub("", _CANARY.sub("", quote), count=1)
    return rest.strip() == ""


def _span_quote(item):
    span = item.get("span") if isinstance(item, dict) else None
    return span.get("quote") if isinstance(span, dict) else None


def drop_reference_markers(value):
    """Remove marker-only spans from UI-facing span lists and graphs (before masking).

    instruction_like_spans entries, graph span nodes, the edges touching those
    nodes and evidence references to them are dropped. Everything else is kept.
    """
    if isinstance(value, list):
        return [drop_reference_markers(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = dict(value)
    if isinstance(result.get("instruction_like_spans"), list):
        result["instruction_like_spans"] = [span for span in result["instruction_like_spans"]
                                            if not (isinstance(span, dict) and is_reference_marker(span.get("quote")))]
    if isinstance(result.get("nodes"), list) and isinstance(result.get("edges"), list):
        hidden = {node.get("id") for node in result["nodes"]
                  if isinstance(node, dict) and node.get("kind") == "span" and is_reference_marker(_span_quote(node))}
        if hidden:
            result["nodes"] = [node for node in result["nodes"] if node.get("id") not in hidden]
            result["edges"] = [edge for edge in result["edges"]
                               if edge.get("source") not in hidden and edge.get("target") not in hidden]
            result = _drop_evidence(result, hidden)
    return {key: drop_reference_markers(item) if key != "instruction_like_spans" else item
            for key, item in result.items()}


def _drop_evidence(value, hidden):
    if isinstance(value, list):
        return [_drop_evidence(item, hidden) for item in value]
    if isinstance(value, dict):
        return {key: ([item for item in entry if item not in hidden] if key == "evidence_node_ids" and isinstance(entry, list)
                      else _drop_evidence(entry, hidden))
                for key, entry in value.items()}
    return value


def marker_node_ids(graph) -> set:
    """Ids of a stored graph's marker-only span nodes (hidden on every display surface)."""
    nodes = (graph or {}).get("nodes") or []
    return {node.get("id") for node in nodes if isinstance(node, dict) and node.get("kind") == "span"
            and is_reference_marker(_span_quote(node))}


def display_model(model, hidden_node_ids=frozenset()):
    """hidden_node_ids: marker node ids known from the stored graph, for payloads (decisions)
    that reference graph nodes without carrying the spans themselves."""
    data = drop_reference_markers(model.model_dump(mode="python"))
    if hidden_node_ids:
        data = _drop_evidence(data, set(hidden_node_ids))
    return type(model).model_validate(mask_canaries(data))
