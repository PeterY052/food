from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional


@dataclass
class LineItem:
    name: str
    qty: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    raw: str = ""


_RE_MONEY = re.compile(r"(?<!\d)(\d{1,5}(?:\.\d{1,2})?)(?!\d)")
_RE_QTY = re.compile(r"(x|×|\*)\s*(\d{1,4}(?:\.\d{1,3})?)", re.IGNORECASE)


_SKIP_KEYWORDS = [
    "合计",
    "总计",
    "应付",
    "实付",
    "找零",
    "优惠",
    "折扣",
    "会员",
    "税",
    "发票",
    "收银",
    "桌",
    "单号",
    "时间",
    "日期",
    "电话",
    "地址",
    "欢迎",
]


def _to_decimal(s: str) -> Optional[Decimal]:
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _clean_line(line: str) -> str:
    line = line.strip()
    # 常见 OCR 噪声
    line = re.sub(r"[|_—–]+", " ", line)
    line = re.sub(r"\s{2,}", " ", line)
    return line


def _looks_like_item(line: str) -> bool:
    if len(line) < 2:
        return False
    if any(k in line for k in _SKIP_KEYWORDS):
        return False
    # 至少包含一个金额数字，才可能是行项目
    if not _RE_MONEY.search(line):
        return False
    # 名称应包含至少一个非数字字符
    if not re.search(r"[^\d\.\s]", line):
        return False
    return True


def parse_receipt_text(text: str) -> list[LineItem]:
    items: list[LineItem] = []
    for raw in text.splitlines():
        line = _clean_line(raw)
        if not line:
            continue
        if not _looks_like_item(line):
            continue

        qty = None
        unit_price = None
        amount = None

        m_qty = _RE_QTY.search(line)
        if m_qty:
            qty = _to_decimal(m_qty.group(2))

        monies = [_to_decimal(m.group(1)) for m in _RE_MONEY.finditer(line)]
        monies = [m for m in monies if m is not None]

        # 经验规则：最后一个数字更可能是行金额；倒数第二个可能是单价
        if monies:
            amount = monies[-1]
            if len(monies) >= 2:
                unit_price = monies[-2]

        # 名称：去掉末尾价格、去掉数量标记
        name = line
        name = re.sub(r"(?:\s+)?\d{1,5}(?:\.\d{1,2})?\s*$", "", name).strip()
        name = _RE_QTY.sub("", name).strip()
        name = re.sub(r"\s{2,}", " ", name)

        # 兜底：如果仍然太短，跳过
        if len(name) < 2:
            continue

        items.append(LineItem(name=name, qty=qty, unit_price=unit_price, amount=amount, raw=raw))

    # 去重（同一行 OCR 可能重复）
    uniq: list[LineItem] = []
    seen: set[str] = set()
    for it in items:
        key = f"{it.name}|{it.qty}|{it.unit_price}|{it.amount}"
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    return uniq


def items_to_rows(items: Iterable[LineItem]) -> list[dict]:
    rows = []
    for it in items:
        rows.append(
            {
                "name": it.name,
                "qty": float(it.qty) if it.qty is not None else None,
                "unit_price": float(it.unit_price) if it.unit_price is not None else None,
                "amount": float(it.amount) if it.amount is not None else None,
                "raw": it.raw,
            }
        )
    return rows

