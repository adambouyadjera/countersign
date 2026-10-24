"""Hide leak-test markers on display surfaces without changing source records."""

import re


_CANARY = re.compile(r"CS-CANARY-[A-Z0-9]{4,}")


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


def display_model(model):
    return type(model).model_validate(mask_canaries(model.model_dump(mode="python")))
