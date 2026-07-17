def _format_optional(value: object | None) -> str:
    """Format an optional value for a plain-text prompt field."""
    if value is None or value == "":
        return "Not specified."
    return str(value)
