"""Деньги храним в целых копейках (int), без float."""

import re

_AMOUNT_RE = re.compile(r"^(\d+)(?:\.(\d{1,2}))?$")


def parse_kopecks(text: str) -> int:
    """'300000.00' -> 30000000. Допускается 0-2 знака после точки, только неотрицательные суммы."""
    value = text.strip().replace(" ", "")
    match = _AMOUNT_RE.match(value)
    if not match:
        raise ValueError(f"не похоже на сумму в рублях: {text!r}")
    rubles, kop = match.group(1), (match.group(2) or "").ljust(2, "0")
    return int(rubles) * 100 + int(kop)


def format_rub(kopecks: int, thousands_sep: str = " ") -> str:
    """30000000 -> '300 000,00'."""
    sign = "-" if kopecks < 0 else ""
    rubles, kop = divmod(abs(kopecks), 100)
    return f"{sign}{rubles:,}".replace(",", thousands_sep) + f",{kop:02d}"


def format_plain(kopecks: int) -> str:
    """30000000 -> '300000.00' (для CSV, без разделителей разрядов)."""
    sign = "-" if kopecks < 0 else ""
    rubles, kop = divmod(abs(kopecks), 100)
    return f"{sign}{rubles}.{kop:02d}"
