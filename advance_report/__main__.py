"""CLI: python -m advance_report --date 2026-11-10 [--data data] [--csv report.csv]"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from .calc import ALLOCATIONS, FIFO, build_report
from .loader import DataError, load
from .render import render_csv, render_text


def _parse_date(value: str) -> date:
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"дата {value!r}: ожидается ГГГГ-ММ-ДД или ДД.ММ.ГГГГ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m advance_report",
        description="Отчёт по оплате работ по договорам на дату.")
    parser.add_argument("--date", required=True, type=_parse_date,
                        help="дата отчёта включительно, например 2026-11-10 или 10.11.2026")
    parser.add_argument("--data", type=Path, default=Path("data"),
                        help="папка с contracts.csv, works.csv, payments.csv (по умолчанию ./data)")
    parser.add_argument("--allocation", choices=ALLOCATIONS, default=FIFO,
                        help="как распределять платежи без привязки к работе: fifo - сначала принятые "
                             "работы по дате приёмки (по умолчанию), proportional - пропорционально остаткам")
    parser.add_argument("--csv", type=Path, metavar="FILE",
                        help="дополнительно сохранить отчёт в CSV для Excel")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        report = build_report(load(args.data), args.date, args.allocation)
    except DataError as exc:
        print(f"Отчёт не построен: {exc}", file=sys.stderr)
        return 2

    print(render_text(report), end="")
    if args.csv:
        args.csv.write_text(render_csv(report), encoding="utf-8-sig", newline="")
        print(f"\nCSV сохранён: {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
