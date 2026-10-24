"""Presentation-only receipt settings; no invoice calculations use this module."""

ACCENT = "blue"
HEADING = "Your receipt"
SHOW_BADGE = False


def receipt_theme() -> dict:
    """Return display settings, not prices or discount rules."""
    return {"accent": ACCENT, "heading": HEADING, "badge": SHOW_BADGE}
