"""Normalize judge-supplied plain text without interpreting it as markup."""

import unicodedata


def normalize_attack_text(text: str) -> str:
    # Preserve word boundaries for layout whitespace; remove other control and
    # format characters, including NUL and invisible bidi/zero-width controls.
    cleaned = "".join(" " if char in "\t\r\n" else char
                      for char in text if char in "\t\r\n"
                      or unicodedata.category(char) not in {"Cc", "Cf"})
    return " ".join(cleaned.split())
