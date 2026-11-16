"""Чтение и проверка CSV из папки data/."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .money import parse_kopecks

WORK_STATUSES = {"planned", "done", "cancelled"}
PAYMENT_STATUSES = {"paid", "queued", "refunded"}


class DataError(Exception):
    """Ошибка во входных данных: отчёт по таким данным строить нельзя."""


@dataclass(frozen=True)
class Contract:
    id: str
    contractor: str
    amount: int  # копейки
    signed_at: date | None


@dataclass(frozen=True)
class Work:
    id: str
    contract_id: str
    title: str
    price: int  # копейки
    status: str
    completed_at: date | None
    cancelled_at: date | None


@dataclass(frozen=True)
class Payment:
    id: str
    contract_id: str
    work_id: str | None
    purpose: str
    amount: int  # копейки
    status: str
    created_at: date | None
    paid_at: date | None
    refunded_at: date | None


@dataclass(frozen=True)
class Dataset:
    contracts: list[Contract]
    works: list[Work]
    payments: list[Payment]


def _date(value: str, where: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DataError(f"{where}: неверная дата {value!r}, ожидается ГГГГ-ММ-ДД") from exc


def _money(value: str, where: str) -> int:
    try:
        return parse_kopecks(value or "")
    except ValueError as exc:
        raise DataError(f"{where}: {exc}") from exc


def _read(path: Path, required: list[str]) -> list[dict[str, str]]:
    if not path.exists():
        raise DataError(f"нет файла {path}")
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise DataError(f"{path.name}: нет колонок {', '.join(missing)}")
        return [{k: (v or "").strip() for k, v in row.items()} for row in reader]


def load(data_dir: Path) -> Dataset:
    contracts: list[Contract] = []
    for row in _read(data_dir / "contracts.csv", ["contract_id", "contractor", "amount", "signed_at"]):
        where = f"contracts.csv, {row['contract_id']}"
        contracts.append(Contract(
            id=row["contract_id"],
            contractor=row["contractor"],
            amount=_money(row["amount"], where),
            signed_at=_date(row["signed_at"], where),
        ))

    works: list[Work] = []
    for row in _read(data_dir / "works.csv",
                     ["work_id", "contract_id", "title", "price", "status", "completed_at", "cancelled_at"]):
        where = f"works.csv, {row['work_id']}"
        works.append(Work(
            id=row["work_id"],
            contract_id=row["contract_id"],
            title=row["title"],
            price=_money(row["price"], where),
            status=row["status"],
            completed_at=_date(row["completed_at"], where),
            cancelled_at=_date(row["cancelled_at"], where),
        ))

    payments: list[Payment] = []
    for row in _read(data_dir / "payments.csv",
                     ["payment_id", "contract_id", "work_id", "purpose", "amount", "status",
                      "created_at", "paid_at", "refunded_at"]):
        where = f"payments.csv, {row['payment_id']}"
        payments.append(Payment(
            id=row["payment_id"],
            contract_id=row["contract_id"],
            work_id=row["work_id"] or None,
            purpose=row["purpose"],
            amount=_money(row["amount"], where),
            status=row["status"],
            created_at=_date(row["created_at"], where),
            paid_at=_date(row["paid_at"], where),
            refunded_at=_date(row["refunded_at"], where),
        ))

    dataset = Dataset(contracts, works, payments)
    validate(dataset)
    return dataset


def validate(ds: Dataset) -> None:
    """Проверки, без которых расчёт был бы неверным. Любая ошибка - отчёт не строим."""
    errors: list[str] = []

    def dupes(ids: list[str], what: str) -> None:
        seen: set[str] = set()
        for i in ids:
            if i in seen:
                errors.append(f"{what} {i} встречается дважды")
            seen.add(i)

    dupes([c.id for c in ds.contracts], "договор")
    dupes([w.id for w in ds.works], "работа")
    dupes([p.id for p in ds.payments], "платёж")

    contract_ids = {c.id for c in ds.contracts}
    work_contract = {w.id: w.contract_id for w in ds.works}

    for w in ds.works:
        if w.contract_id not in contract_ids:
            errors.append(f"работа {w.id}: нет договора {w.contract_id}")
        if w.status not in WORK_STATUSES:
            errors.append(f"работа {w.id}: неизвестный статус {w.status!r}")
        if w.status == "done" and w.completed_at is None:
            errors.append(f"работа {w.id}: статус done, но нет completed_at")
        if w.status == "cancelled" and w.cancelled_at is None:
            errors.append(f"работа {w.id}: статус cancelled, но нет cancelled_at")
        if w.status != "done" and w.completed_at is not None:
            errors.append(f"работа {w.id}: статус {w.status}, но заполнен completed_at")
        if w.status != "cancelled" and w.cancelled_at is not None:
            errors.append(f"работа {w.id}: статус {w.status}, но заполнен cancelled_at")

    for p in ds.payments:
        if p.contract_id not in contract_ids:
            errors.append(f"платёж {p.id}: нет договора {p.contract_id}")
        if p.work_id is not None:
            if p.work_id not in work_contract:
                errors.append(f"платёж {p.id}: нет работы {p.work_id}")
            elif work_contract[p.work_id] != p.contract_id:
                errors.append(f"платёж {p.id}: работа {p.work_id} из другого договора")
        if p.status not in PAYMENT_STATUSES:
            errors.append(f"платёж {p.id}: неизвестный статус {p.status!r}")
        if p.status in ("paid", "refunded") and p.paid_at is None:
            errors.append(f"платёж {p.id}: статус {p.status}, но нет paid_at")
        if p.status == "refunded" and p.refunded_at is None:
            errors.append(f"платёж {p.id}: статус refunded, но нет refunded_at")
        if p.status == "queued" and p.paid_at is not None:
            errors.append(f"платёж {p.id}: статус queued, но заполнен paid_at")
        if p.status != "refunded" and p.refunded_at is not None:
            errors.append(f"платёж {p.id}: статус {p.status}, но заполнен refunded_at")
        if p.created_at is None:
            errors.append(f"платёж {p.id}: нет created_at")
        if p.created_at and p.paid_at and p.paid_at < p.created_at:
            errors.append(f"платёж {p.id}: оплачен раньше, чем заведён в учёт")
        if p.refunded_at and p.paid_at and p.refunded_at < p.paid_at:
            errors.append(f"платёж {p.id}: возврат раньше оплаты")

    if errors:
        raise DataError("ошибки во входных данных:\n  - " + "\n  - ".join(errors))
