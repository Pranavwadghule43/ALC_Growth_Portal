def safe_csv(value: object) -> object:
    """Prevent spreadsheet applications from interpreting untrusted CSV cells as formulas."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value
