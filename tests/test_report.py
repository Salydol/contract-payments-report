import contextlib
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path

from advance_report.__main__ import main
from advance_report.calc import CANCELLED, DONE, PLANNED, build_contract, build_report
from advance_report.loader import Contract, DataError, Payment, Work, load
from advance_report.money import format_rub, parse_kopecks

DATA = Path(__file__).resolve().parent.parent / "data"
RUB = 100  # копеек в рубле


def W(id, price, status="planned", completed=None, cancelled=None, contract="C"):
    return Work(id, contract, id, price * RUB, status, completed, cancelled)


def P(id, amount, status="paid", work=None, created=date(2026, 1, 1), paid=date(2026, 1, 1),
      refunded=None, contract="C"):
    if status == "queued":
        paid = None
    return Payment(id, contract, work, "", amount * RUB, status, created, paid, refunded)


C = Contract("C", "Подрядчик", 0, date(2026, 1, 1))
ON = date(2026, 6, 1)


class MoneyTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_kopecks("300000.00"), 30_000_000)
        self.assertEqual(parse_kopecks("0.5"), 50)
        self.assertEqual(parse_kopecks("12"), 1200)
        for bad in ("", "1.234", "-5", "1,5", "abc"):
            with self.assertRaises(ValueError):
                parse_kopecks(bad)

    def test_no_float_rounding(self):
        # 0.1 + 0.2 во float даёт 0.30000000000000004; в копейках - ровно 30
        self.assertEqual(parse_kopecks("0.10") + parse_kopecks("0.20"), parse_kopecks("0.30"))

    def test_format(self):
        self.assertEqual(format_rub(30_000_005), "300 000,05")
        self.assertEqual(format_rub(-150), "-1,50")


class AllocationTest(unittest.TestCase):
    def test_pool_goes_to_done_works_by_acceptance_date_then_planned(self):
        works = [W("A", 100), W("B", 100, "done", date(2026, 3, 1)), W("C1", 100, "done", date(2026, 2, 1))]
        r = build_contract(C, works, [P("p", 250)], ON)
        got = {l.work.id: l.paid for l in r.works}
        self.assertEqual(got, {"C1": 100 * RUB, "B": 100 * RUB, "A": 50 * RUB})
        self.assertEqual(r.remaining_total, 50 * RUB)
        self.assertEqual(r.remaining_done, 0)

    def test_linked_payment_applied_first(self):
        works = [W("A", 100, "done", date(2026, 2, 1)), W("B", 100, "done", date(2026, 3, 1))]
        r = build_contract(C, works, [P("p1", 100), P("p2", 30, work="B")], ON)
        b = next(l for l in r.works if l.work.id == "B")
        self.assertEqual((b.paid_direct, b.paid_from_pool), (30 * RUB, 0))
        self.assertEqual(r.remaining_done, 70 * RUB)

    def test_cancelled_work_gets_nothing_and_is_not_owed(self):
        works = [W("A", 100, "cancelled", cancelled=date(2026, 2, 1)), W("B", 100)]
        r = build_contract(C, works, [P("p", 150)], ON)
        a = next(l for l in r.works if l.work.id == "A")
        self.assertEqual((a.state, a.due, a.paid), (CANCELLED, 0, 0))
        self.assertEqual(r.overpaid, 50 * RUB)
        self.assertEqual(r.remaining_total, 0)

    def test_linked_payment_to_cancelled_work_moves_to_pool(self):
        works = [W("A", 100, "cancelled", cancelled=date(2026, 2, 1)), W("B", 100, "done", date(2026, 2, 1))]
        r = build_contract(C, works, [P("p", 40, work="A")], ON)
        b = next(l for l in r.works if l.work.id == "B")
        self.assertEqual(b.paid_from_pool, 40 * RUB)
        self.assertTrue(any("сверх цены" in w for w in r.warnings))

    def test_overpayment_reported(self):
        r = build_contract(C, [W("A", 100, "done", date(2026, 2, 1))], [P("p1", 80), P("p2", 30)], ON)
        self.assertEqual(r.overpaid, 10 * RUB)
        self.assertEqual(r.works[0].remaining, 0)


class AsOfDateTest(unittest.TestCase):
    def test_work_done_after_report_date_is_planned(self):
        r = build_contract(C, [W("A", 100, "done", date(2026, 7, 1))], [], ON)
        self.assertEqual(r.works[0].state, PLANNED)

    def test_report_date_is_inclusive(self):
        r = build_contract(C, [W("A", 100, "done", ON)], [P("p", 100, paid=ON, created=ON)], ON)
        self.assertEqual(r.works[0].state, DONE)
        self.assertEqual(r.paid_total, 100 * RUB)

    def test_payment_after_date_ignored_or_queued(self):
        later = date(2026, 7, 1)
        pays = [P("future", 10, created=later, paid=later),
                P("queued_then", 20, created=date(2026, 5, 1), paid=later)]
        r = build_contract(C, [W("A", 100)], pays, ON)
        states = {pl.payment.id: pl.state for pl in r.payments}
        self.assertEqual(states, {"future": None, "queued_then": "queued"})
        self.assertEqual((r.paid_total, r.queued_total), (0, 20 * RUB))

    def test_refund_counts_only_after_refund_date(self):
        p = P("p", 50, status="refunded", paid=date(2026, 2, 1), refunded=date(2026, 3, 1))
        before = build_contract(C, [W("A", 100)], [p], date(2026, 2, 15))
        after = build_contract(C, [W("A", 100)], [p], date(2026, 3, 1))
        self.assertEqual(before.paid_total, 50 * RUB)
        self.assertEqual((after.paid_total, after.refunded_total), (0, 50 * RUB))

    def test_queued_payment_is_not_paid(self):
        r = build_contract(C, [W("A", 100)], [P("p", 100, status="queued")], ON)
        self.assertEqual((r.paid_total, r.queued_total, r.remaining_total), (0, 100 * RUB, 100 * RUB))


class RealDataTest(unittest.TestCase):
    """Контрольные цифры по data/ на 10.11.2026 - сверены вручную (см. DECISIONS.md)."""

    def setUp(self):
        self.report = build_report(load(DATA), date(2026, 11, 10))
        self.by_id = {c.contract.id: c for c in self.report.contracts}

    def test_d1(self):
        d1 = self.by_id["D-1"]
        paid = {l.work.id: l.paid // RUB for l in d1.works}
        self.assertEqual(paid, {"W-11": 100000, "W-12": 100000, "W-13": 0,
                                "W-14": 100000, "W-15": 15000, "W-16": 0})
        self.assertEqual(d1.paid_total, 315000 * RUB)
        self.assertEqual(d1.remaining_total, 185000 * RUB)
        self.assertEqual(d1.remaining_done, 0)
        self.assertEqual(d1.queued_total, 150000 * RUB)
        self.assertEqual(d1.refunded_total, 50000 * RUB)

    def test_d2(self):
        d2 = self.by_id["D-2"]
        w23 = next(l for l in d2.works if l.work.id == "W-23")
        self.assertEqual(w23.state, PLANNED)  # принята 11.11 - после даты отчёта
        self.assertEqual(d2.paid_total, 75000 * RUB)  # P-8 от 12.11 не входит
        self.assertEqual(d2.remaining_total, 15000 * RUB)
        self.assertEqual(d2.overpaid, 0)

    def test_d2_on_12_11_shows_overpayment(self):
        d2 = {c.contract.id: c for c in build_report(load(DATA), date(2026, 11, 12)).contracts}["D-2"]
        self.assertEqual((d2.paid_total, d2.overpaid, d2.remaining_total), (95000 * RUB, 5000 * RUB, 0))

    def test_paid_always_reconciles(self):
        for d in (date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 10), date(2026, 12, 31)):
            for c in build_report(load(DATA), d).contracts:
                self.assertEqual(sum(l.paid for l in c.works) + c.overpaid, c.paid_total)
                self.assertEqual(c.due_total - c.remaining_total, sum(l.paid for l in c.works))


class LoaderTest(unittest.TestCase):
    def _write(self, d: Path, payments_row: str):
        (d / "contracts.csv").write_text("contract_id,contractor,amount,signed_at\nD-1,X,100.00,2026-01-01\n",
                                         encoding="utf-8")
        (d / "works.csv").write_text(
            "work_id,contract_id,title,price,status,completed_at,cancelled_at\nW-1,D-1,t,100.00,planned,,\n",
            encoding="utf-8")
        (d / "payments.csv").write_text(
            "payment_id,contract_id,work_id,purpose,amount,status,created_at,paid_at,refunded_at\n"
            + payments_row + "\n", encoding="utf-8")

    def test_bad_data_is_rejected(self):
        bad_rows = [
            "P-1,D-1,W-9,x,10.00,paid,2026-01-01,2026-01-01,",   # нет такой работы
            "P-1,D-1,,x,10.00,paid,2026-01-01,,",                 # paid без даты оплаты
            "P-1,D-1,,x,10.00,weird,2026-01-01,,",                # неизвестный статус
            "P-1,D-1,,x,10.555,paid,2026-01-01,2026-01-01,",      # дробные копейки
        ]
        for row in bad_rows:
            with self.subTest(row=row), tempfile.TemporaryDirectory() as tmp:
                self._write(Path(tmp), row)
                with self.assertRaises(DataError):
                    load(Path(tmp))

    def test_cli_runs_and_writes_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "r.csv"
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["--date", "10.11.2026", "--data", str(DATA), "--csv", str(out)])
            self.assertEqual(code, 0)
            text = out.read_text(encoding="utf-8-sig")
            self.assertIn("ИТОГО по договору", text)
            self.assertIn("185000,00", text)


if __name__ == "__main__":
    unittest.main()
