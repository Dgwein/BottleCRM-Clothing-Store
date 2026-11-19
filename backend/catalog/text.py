"""Text keys and size labels for matching catalog values."""

import re
import unicodedata

_SPACES = re.compile(r"\s+")

# The size of a variant whose source names none.
UNIQUE_SIZE = "Unique"


def normalize_key(text) -> str:
    """The key two spellings of one value share: accents dropped, case
    folded, whitespace collapsed. "Genérica " and "generica" match."""
    decomposed = unicodedata.normalize("NFKD", str(text))
    bare = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _SPACES.sub(" ", bare).strip().casefold()


# Raw size labels differ in spelling across listings ("0-3 M", "00-03 Meses",
# "0 Meses - 3 Meses"); these patterns rewrite the spelling only.
_MONTHS = r"(?:m|mes|meses)"
_YEARS = r"(?:a|ano|anos)"
_MONTH_RANGE = re.compile(rf"^(\d{{1,2}})\s*{_MONTHS}?\s*-\s*(\d{{1,2}})\s*{_MONTHS}$")
_MONTH = re.compile(rf"^(\d{{1,2}})\s*{_MONTHS}$")
_YEAR_RANGE = re.compile(rf"^(\d{{1,2}})\s*{_YEARS}?\s*-\s*(\d{{1,2}})\s*{_YEARS}$")
_YEAR = re.compile(rf"^(\d{{1,2}})\s*{_YEARS}$")
_CM = re.compile(r"^(\d{1,3})\s*cm$")
_NEWBORN = {"rn", "recien nacido", "recien nacida"}
_UNIQUE = {"", "unique", "unica", "talla unica"}

# The store's decisions on labels the spelling rules cannot settle: one size
# written two ways, or a label with no unit.
#   * a bare number from 1 to 16 is years ("2" is 2A); larger ones (100, 110,
#     120) are heights and stay as written;
#   * a bare range is months ("6-9" is 6-9 meses);
#   * whole years written in months are years ("24 meses" is 2A), except
#     12 meses, which stays months: 1A is written 12 meses;
#   * 0 meses is RN.
_BARE_NUMBER = re.compile(r"^(\d{1,2})$")
_BARE_RANGE = re.compile(r"^(\d{1,2})\s*-\s*(\d{1,2})$")
_MONTHS_LABEL = re.compile(r"^(\d{1,3}) meses$")
SIZE_DECISIONS = {
    "0 meses": "RN",
    "1A": "12 meses",
}


def _spelled(text, key):
    if key in _NEWBORN:
        return "RN"
    if m := _MONTH_RANGE.match(key):
        return f"{int(m[1])}-{int(m[2])} meses"
    if m := _MONTH.match(key):
        return f"{int(m[1])} meses"
    if m := _YEAR_RANGE.match(key):
        return f"{int(m[1])}-{int(m[2])} años"
    if m := _YEAR.match(key):
        return f"{int(m[1])}A"
    if m := _CM.match(key):
        return f"{int(m[1])} cm"
    return text


def _decided(label):
    if (m := _BARE_NUMBER.match(label)) and 1 <= int(m[1]) <= 16:
        label = f"{int(m[1])}A"
    elif m := _BARE_RANGE.match(label):
        label = f"{int(m[1])}-{int(m[2])} meses"
    elif (m := _MONTHS_LABEL.match(label)) and int(m[1]) >= 24 and int(m[1]) % 12 == 0:
        label = f"{int(m[1]) // 12}A"
    return SIZE_DECISIONS.get(label, label)


def canonical_size(raw) -> str:
    """The store's label for a size; "Unique" when there is none."""
    text = _SPACES.sub(" ", str(raw if raw is not None else "")).strip()
    key = normalize_key(text)
    if key in _UNIQUE:
        return UNIQUE_SIZE
    return _decided(_spelled(text, key))[:50]


def size_sort_order(label) -> int:
    """Where a size sorts: Unique, RN, then by the oldest age it fits and the
    youngest, so "0-3 meses" < "3 meses" < "3-6 meses" < "2A". Lengths in cm
    follow ages, then bare numbers by value, then anything else."""
    size = canonical_size(label)
    key = normalize_key(size)
    if size == UNIQUE_SIZE:
        return 0
    if key == "rn":
        return 1
    if m := _MONTH_RANGE.match(key):
        low, high = int(m[1]), int(m[2])
    elif m := _MONTH.match(key):
        low = high = int(m[1])
    elif m := _YEAR_RANGE.match(key):
        low, high = int(m[1]) * 12, int(m[2]) * 12
    elif m := _YEAR.match(key):
        low = high = int(m[1]) * 12
    elif m := _CM.match(key):
        return 500_000 + int(m[1])
    elif key.isdigit():
        return 800_000 + int(key)
    else:
        return 900_000
    return 2 + high * 1000 + low
