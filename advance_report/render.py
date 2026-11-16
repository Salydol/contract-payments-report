"""Вывод отчёта: таблица в консоль и CSV для Excel."""

from __future__ import annotations

import csv
import io
from datetime import date

from .calc import CANCELLED, PAID, QUEUED, REFUNDED, STATUS_RU, ContractReport, Report
from .money import format_plain, format_rub


def _d(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else ""


def _payment_status(pl, on: date) -> str:
    p = pl.payment
    if pl.state is None:
        return f"не учтён: заведён {_d(p.created_at)}, после даты отчёта"
    if pl.state == PAID:
        return f"выплачен {_d(p.paid_at)}"
    if pl.state == REFUNDED:
        return f"выплачен {_d(p.paid_at)}, возвращён {_d(p.refunded_at)} - не считается"
    if pl.state == QUEUED:
        tail = f", оплачен {_d(p.paid_at)} - после даты отчёта" if p.paid_at else ""
        return f"в очереди, не выплачен{tail}"
    return pl.state


def _table(headers: list[str], rows: list[list[str]], right: set[int]) -> str:
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]

    def fmt(cells: list[str]) -> str:
        return "  ".join(c.rjust(widths[i]) if i in right else c.ljust(widths[i])
                         for i, c in enumerate(cells)).rstrip()

    sep = "  ".join("-" * w for w in widths)
    return "\n".join([fmt(headers), sep, *(fmt(r) for r in rows)])


def _contract_text(cr: ContractReport, on: date) -> str:
    c = cr.contract
    out = [f"Договор {c.id} - {c.contractor}, подписан {_d(c.signed_at)}, сумма {format_rub(c.amount)}", ""]

    rows = []
    for wl in cr.works:
        w = wl.work
        rows.append([
            w.id, w.title, STATUS_RU[wl.state], format_rub(w.price),
            format_rub(wl.paid_direct), format_rub(wl.paid_from_pool), format_rub(wl.paid),
            format_rub(wl.remaining) if wl.state != CANCELLED else "-",
        ])
    out.append(_table(
        ["Работа", "Название", "Статус на дату", "Цена",
         "Оплачено по работе", "Зачтено из общих", "Выплачено", "Осталось"],
        rows, right={3, 4, 5, 6, 7}))
    out.append("")

    totals = [
        ("Цена работ к оплате (без отменённых)", cr.due_total),
        ("Выплачено", cr.paid_total),
        ("Осталось оплатить", cr.remaining_total),
        ("  в т.ч. за принятые работы (долг сейчас)", cr.remaining_done),
        ("  за ещё не принятые работы", cr.remaining_total - cr.remaining_done),
        ("Предоплата за ещё не принятые работы", cr.prepaid_planned),
        ("В очереди на оплату (не выплачено)", cr.queued_total),
        ("Возвращено подрядчиком", cr.refunded_total),
        ("Переплата", cr.overpaid),
    ]
    width = max(len(t[0]) for t in totals)
    num_width = max(len(format_rub(t[1])) for t in totals)
    out.append(f"Итого по договору {c.id}:")
    out.extend(f"  {label.ljust(width)}  {format_rub(v).rjust(num_width)}" for label, v in totals)
    out.append("")

    out.append("Платежи:")
    prow = []
    for pl in cr.payments:
        p = pl.payment
        prow.append([p.id, p.work_id or "общий", p.purpose, format_rub(p.amount), _payment_status(pl, on)])
    out.append(_table(["Платёж", "Работа", "Назначение", "Сумма", "На дату отчёта"], prow, right={3}))

    if cr.warnings:
        out.append("")
        out.append("Обратите внимание:")
        out.extend(f"  - {w}" for w in cr.warnings)
    return "\n".join(out)


def render_text(report: Report) -> str:
    parts = [f"Отчёт по оплате работ на {_d(report.on)} (включительно). Суммы в рублях.", ""]
    for cr in report.contracts:
        parts.append("=" * 100)
        parts.append(_contract_text(cr, report.on))
        parts.append("")

    parts.append("=" * 100)
    parts.append("Итого по всем договорам:")
    total = [
        ("Выплачено", sum(c.paid_total for c in report.contracts)),
        ("Осталось оплатить", sum(c.remaining_total for c in report.contracts)),
        ("  в т.ч. за принятые работы", sum(c.remaining_done for c in report.contracts)),
        ("В очереди на оплату", sum(c.queued_total for c in report.contracts)),
        ("Переплата", sum(c.overpaid for c in report.contracts)),
    ]
    width = max(len(t[0]) for t in total)
    parts.extend(f"  {label.ljust(width)}  {format_rub(v).rjust(14)}" for label, v in total)
    return "\n".join(parts) + "\n"


CSV_COLUMNS = [
    "Дата отчёта", "Договор", "Подрядчик", "Работа", "Название", "Статус на дату",
    "Цена", "К оплате", "Оплачено по работе", "Зачтено из общих платежей", "Выплачено", "Осталось",
    "В очереди", "Возвращено", "Переплата",
]


def render_csv(report: Report) -> str:
    """CSV с разделителем ';' и запятой в копейках - так его без настроек открывает русский Excel."""
    def m(kop: int) -> str:
        return format_plain(kop).replace(".", ",")

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    on = _d(report.on)
    for cr in report.contracts:
        c = cr.contract
        for wl in cr.works:
            writer.writerow([
                on, c.id, c.contractor, wl.work.id, wl.work.title, STATUS_RU[wl.state],
                m(wl.work.price), m(wl.due), m(wl.paid_direct), m(wl.paid_from_pool), m(wl.paid),
                m(wl.remaining), m(wl.queued), "", "",
            ])
        writer.writerow([
            on, c.id, c.contractor, "", "ИТОГО по договору", "",
            m(cr.works_total), m(cr.due_total), m(sum(w.paid_direct for w in cr.works)),
            m(sum(w.paid_from_pool for w in cr.works)), m(cr.paid_total), m(cr.remaining_total),
            m(cr.queued_total), m(cr.refunded_total), m(cr.overpaid),
        ])
    return buf.getvalue()
