"""Расчёт отчёта на дату.

Порядок (обоснование - в DECISIONS.md):
1. Статусы работ и платежей восстанавливаются на дату отчёта по датам, а не берутся из выгрузки.
2. Выплачено = платежи, деньги по которым ушли не позже даты отчёта и не вернулись к этой дате.
3. Платёж, привязанный к работе, идёт в эту работу (не больше того, что по ней положено);
   излишек и все непривязанные платежи - в общий котёл договора.
4. Котёл распределяется: сначала на принятые работы по дате приёмки, затем на запланированные
   по номеру работы. Отменённые работы ничего не получают. Остаток котла - переплата по договору.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .loader import Contract, Dataset, Payment, Work
from .money import format_rub

# Состояния на дату отчёта
PLANNED, DONE, CANCELLED = "planned", "done", "cancelled"
PAID, QUEUED, REFUNDED = "paid", "queued", "refunded"

STATUS_RU = {
    PLANNED: "запланирована",
    DONE: "принята",
    CANCELLED: "отменена",
}


def work_state(w: Work, on: date) -> str:
    """Статус работы на дату. Если работу приняли/отменили позже даты - на дату она ещё запланирована."""
    if w.status == DONE and w.completed_at is not None and w.completed_at <= on:
        return DONE
    if w.status == CANCELLED and w.cancelled_at is not None and w.cancelled_at <= on:
        return CANCELLED
    return PLANNED


def payment_state(p: Payment, on: date) -> str | None:
    """Статус платежа на дату. None - на эту дату платежа ещё не было в учёте."""
    if p.paid_at is not None and p.paid_at <= on:
        if p.refunded_at is not None and p.refunded_at <= on:
            return REFUNDED
        return PAID
    if p.created_at is not None and p.created_at <= on:
        return QUEUED
    return None


@dataclass
class WorkLine:
    work: Work
    state: str
    due: int            # сколько положено заплатить за работу на дату (0 для отменённой)
    paid_direct: int = 0      # из платежей, привязанных к этой работе
    paid_from_pool: int = 0   # из общих платежей по договору (аванс и т.п.)
    queued: int = 0           # привязанные к работе платежи в очереди

    @property
    def paid(self) -> int:
        return self.paid_direct + self.paid_from_pool

    @property
    def remaining(self) -> int:
        return self.due - self.paid


@dataclass
class PaymentLine:
    payment: Payment
    state: str | None   # состояние на дату; None - платежа ещё не было
    note: str = ""


@dataclass
class ContractReport:
    contract: Contract
    works: list[WorkLine]
    payments: list[PaymentLine]
    overpaid: int = 0                       # выплачено сверх цены всех неотменённых работ
    warnings: list[str] = field(default_factory=list)

    @property
    def works_total(self) -> int:
        return sum(w.work.price for w in self.works)

    @property
    def cancelled_total(self) -> int:
        return sum(w.work.price for w in self.works if w.state == CANCELLED)

    @property
    def due_total(self) -> int:
        return sum(w.due for w in self.works)

    @property
    def paid_total(self) -> int:
        """Всего выплачено по договору на дату (нетто, с учётом возвратов)."""
        return sum(p.payment.amount for p in self.payments if p.state == PAID)

    @property
    def refunded_total(self) -> int:
        return sum(p.payment.amount for p in self.payments if p.state == REFUNDED)

    @property
    def queued_total(self) -> int:
        return sum(p.payment.amount for p in self.payments if p.state == QUEUED)

    @property
    def remaining_total(self) -> int:
        return sum(w.remaining for w in self.works)

    @property
    def remaining_done(self) -> int:
        """Долг за уже принятые работы - то, что надо платить сейчас."""
        return sum(w.remaining for w in self.works if w.state == DONE)

    @property
    def prepaid_planned(self) -> int:
        """Сколько из выплаченного - предоплата за ещё не принятые работы."""
        return sum(w.paid for w in self.works if w.state == PLANNED)


@dataclass
class Report:
    on: date
    contracts: list[ContractReport]


def _allocation_order(lines: list[WorkLine]) -> list[WorkLine]:
    done = sorted((l for l in lines if l.state == DONE),
                  key=lambda l: (l.work.completed_at, l.work.id))
    planned = sorted((l for l in lines if l.state == PLANNED), key=lambda l: l.work.id)
    return done + planned


def build_contract(contract: Contract, works: list[Work], payments: list[Payment], on: date) -> ContractReport:
    lines = []
    for w in sorted(works, key=lambda w: w.id):
        state = work_state(w, on)
        lines.append(WorkLine(work=w, state=state, due=0 if state == CANCELLED else w.price))
    by_id = {l.work.id: l for l in lines}

    pay_lines = []
    for p in sorted(payments, key=lambda p: (p.paid_at or p.created_at or date.max, p.id)):
        pay_lines.append(PaymentLine(payment=p, state=payment_state(p, on)))

    report = ContractReport(contract=contract, works=lines, payments=pay_lines)

    # 1. Привязанные платежи - в свою работу, излишек - в общий котёл.
    pool = 0
    for pl in pay_lines:
        p = pl.payment
        if pl.state == QUEUED and p.work_id:
            by_id[p.work_id].queued += p.amount
        if pl.state != PAID:
            continue
        if p.work_id is None:
            pool += p.amount
            continue
        line = by_id[p.work_id]
        take = max(0, min(p.amount, line.due - line.paid_direct))
        line.paid_direct += take
        if take < p.amount:
            pool += p.amount - take
            pl.note = (f"{_rub(p.amount - take)} сверх цены работы {line.work.id} "
                       f"зачтено как общий платёж по договору")
            report.warnings.append(f"Платёж {p.id}: {pl.note}.")

    # 2. Общий котёл - по принятым работам (по дате приёмки), затем по запланированным.
    for line in _allocation_order(lines):
        if pool == 0:
            break
        take = min(pool, line.remaining)
        line.paid_from_pool += take
        pool -= take
    report.overpaid = pool

    # 3. Сверки, которые бухгалтеру стоит увидеть.
    for line in lines:
        w = line.work
        if w.status == DONE and line.state != DONE:
            report.warnings.append(
                f"Работа {w.id} принята {w.completed_at:%d.%m.%Y}, после даты отчёта - на дату считаю её запланированной.")
        if w.status == CANCELLED and line.state != CANCELLED:
            report.warnings.append(
                f"Работа {w.id} отменена {w.cancelled_at:%d.%m.%Y}, после даты отчёта - на дату считаю её запланированной.")
    if contract.amount != report.works_total:
        report.warnings.append(
            f"Сумма договора {_rub(contract.amount)} не равна сумме цен работ {_rub(report.works_total)}.")
    if report.cancelled_total:
        report.warnings.append(
            f"Отменены работы на {_rub(report.cancelled_total)}: к оплате по договору считаю "
            f"{_rub(report.due_total)} вместо {_rub(contract.amount)}.")
    if report.overpaid:
        report.warnings.append(f"Переплата по договору: {_rub(report.overpaid)} выплачено сверх цены работ.")
    if report.queued_total and report.queued_total != report.remaining_total:
        report.warnings.append(
            f"В очереди на оплату {_rub(report.queued_total)}, а осталось оплатить "
            f"{_rub(report.remaining_total)} - суммы не сходятся.")
    if report.queued_total > report.remaining_total:
        report.warnings.append("Платежи в очереди больше остатка: после их оплаты будет переплата.")
    return report


def build_report(ds: Dataset, on: date) -> Report:
    reports = []
    for c in sorted(ds.contracts, key=lambda c: c.id):
        works = [w for w in ds.works if w.contract_id == c.id]
        payments = [p for p in ds.payments if p.contract_id == c.id]
        reports.append(build_contract(c, works, payments, on))
    return Report(on=on, contracts=reports)


def _rub(kop: int) -> str:
    return format_rub(kop) + " ₽"
