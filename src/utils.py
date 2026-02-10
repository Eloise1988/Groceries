import re
from datetime import datetime, timezone


def normalize_item(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def parse_item(text: str):
    raw = text.strip()
    if not raw:
        return None
    return raw


def parse_items(text: str):
    raw = text.strip()
    if not raw:
        return []

    # Support commas/newlines/semicolons and common unicode comma variants.
    parts = [part.strip() for part in re.split(r"[,\n;，、،]+", raw)]
    return [part for part in parts if part and not part.startswith("/")]


_UNITS = {
    "teaspoon", "teaspoons", "tsp", "tsp.",
    "tablespoon", "tablespoons", "tbsp", "tbsp.",
    "cup", "cups",
    "ounce", "ounces", "oz", "oz.",
    "pound", "pounds", "lb", "lb.",
    "gram", "grams", "g",
    "kilogram", "kilograms", "kg",
    "milliliter", "milliliters", "ml",
    "liter", "liters", "l",
    "clove", "cloves",
    "slice", "slices",
    "can", "cans",
    "package", "packages", "pkg", "pkg.",
    "pinch", "pinches",
    "dash", "dashes",
}


def simplify_ingredient(text: str) -> str:
    raw = re.sub(r"\s+", " ", text.strip())
    if not raw:
        return raw

    parts = raw.split()
    if not parts:
        return raw

    first = parts[0]
    second = parts[1] if len(parts) > 1 else ""

    # Remove leading quantity like "1", "1/2", "2.5"
    if re.fullmatch(r"\d+([./]\d+)?", first):
        if second.lower().rstrip(".") in _UNITS:
            return " ".join(parts[2:]).strip() or raw
        return " ".join(parts[1:]).strip() or raw

    # Remove leading fraction like "1/2"
    if re.fullmatch(r"\d+/\d+", first):
        if second.lower().rstrip(".") in _UNITS:
            return " ".join(parts[2:]).strip() or raw
        return " ".join(parts[1:]).strip() or raw

    return raw


def now_utc():
    return datetime.now(timezone.utc)
