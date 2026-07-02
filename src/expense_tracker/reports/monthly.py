"""Monthly report generation and rendering."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, Field

from expense_tracker.schemas.domain import ReceiptItemRecord, ReceiptRecord, ReceiptStore
from expense_tracker.schemas.enums import ItemCategory
from expense_tracker.schemas.owners import OwnersConfig, load_owners_config
from expense_tracker.storage.json_store import DEFAULT_STORE_PATH, load_receipt_store


DEFAULT_REPORTS_DIR = Path("reports")
PRICE_RANKING_EXCLUDED_NAME_PATTERNS = ("leergut", "pfand", "flaschenpfand", "mehrwegpfand")
MONTHLY_REPORT_SCHEMA_NAME = "expense_tracker.monthly_report"
MONTHLY_REPORT_SCHEMA_VERSION = "1.0"


def _money(value: Decimal | int | str) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    return start, end


def _quarter_start_month(month: int) -> int:
    return ((month - 1) // 3) * 3 + 1


def _safe_percent_change(current: Decimal, previous: Decimal) -> Decimal | None:
    if previous == 0:
        return None
    return ((current - previous) / previous) * Decimal("100")


def _format_month(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _load_owners_map(owners_path: str | Path | None) -> tuple[dict[str, str], str | None]:
    if owners_path is None:
        return {}, None

    path = Path(owners_path)
    if not path.exists():
        return {}, None

    config: OwnersConfig = load_owners_config(path)
    owner_names = {owner.id: owner.name for owner in config.owners}
    me_owner_id = next((owner.id for owner in config.owners if owner.is_me), None)
    return owner_names, me_owner_id


def _is_price_ranking_item(item: ReceiptItemRecord) -> bool:
    if item.category == ItemCategory.DINING:
        return False
    if item.total_price < 0:
        return False

    haystack = f"{item.name} {item.normalized_name}".strip().lower()
    return not any(pattern in haystack for pattern in PRICE_RANKING_EXCLUDED_NAME_PATTERNS)


def _sum_receipts(receipts: Iterable[ReceiptRecord]) -> Decimal:
    return sum((receipt.total_amount for receipt in receipts), start=Decimal("0"))


def _sum_items(items: Iterable[ReceiptItemRecord]) -> Decimal:
    return sum((item.total_price for item in items), start=Decimal("0"))


def _sum_owner_totals(receipts: Iterable[ReceiptRecord]) -> dict[str, Decimal]:
    """Sum item total_prices per owner_id from a list of receipts."""
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for receipt in receipts:
        for item in receipt.items:
            totals[item.owner_id] += item.total_price
    return dict(totals)


class ReportMeta(BaseModel):
    schema_name: str = MONTHLY_REPORT_SCHEMA_NAME
    schema_version: str = MONTHLY_REPORT_SCHEMA_VERSION
    report_month: str
    quarter: str
    generated_at: datetime
    currency: str = "EUR"
    receipt_count: int = Field(ge=0)
    item_count: int = Field(ge=0)


class ReportOverview(BaseModel):
    month_total_spend: Decimal
    quarter_total_spend: Decimal
    month_over_month_change: Decimal | None = None
    year_over_year_change: Decimal | None = None
    my_month_total_spend: Decimal | None = None
    top_category: str | None = None


class OwnerSpendRow(BaseModel):
    owner_id: str
    owner_name: str
    total_spend: Decimal
    share_percent: Decimal | None = None
    month_over_month_change: Decimal | None = None
    year_over_year_change: Decimal | None = None


class CategorySpendRow(BaseModel):
    category: str
    total_spend: Decimal
    share_percent: Decimal | None = None


class PriceChangeRow(BaseModel):
    normalized_name: str
    display_name: str
    previous_unit_price: Decimal
    current_unit_price: Decimal
    change_amount: Decimal
    change_percent: Decimal | None = None
    latest_purchase_date: date


class ReportHighlights(BaseModel):
    summary: list[str] = Field(default_factory=list)


class ReportDataQuality(BaseModel):
    failed_ocr_count: int = Field(ge=0)
    pending_review_count: int = Field(ge=0)
    removed_items_count: int = Field(ge=0)


class MonthlyReport(BaseModel):
    meta: ReportMeta
    overview: ReportOverview
    owner_spend: list[OwnerSpendRow] = Field(default_factory=list)
    category_spend: list[CategorySpendRow] = Field(default_factory=list)
    price_increases: list[PriceChangeRow] = Field(default_factory=list)
    price_decreases: list[PriceChangeRow] = Field(default_factory=list)
    highlights: ReportHighlights = Field(default_factory=ReportHighlights)
    data_quality: ReportDataQuality


@dataclass
class WrittenMonthlyReport:
    report: MonthlyReport
    json_path: Path
    html_path: Path
    schema_path: Path | None = None


def _month_receipts(store: ReceiptStore, year: int, month: int) -> list[ReceiptRecord]:
    start, end = _month_bounds(year, month)
    return [
        receipt
        for receipt in store.receipts
        if start <= receipt.purchase_date < end
    ]


def _quarter_receipts(store: ReceiptStore, year: int, month: int) -> list[ReceiptRecord]:
    quarter_start_month = _quarter_start_month(month)
    start, _ = _month_bounds(year, quarter_start_month)
    if quarter_start_month == 10:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, quarter_start_month + 3, 1)
    return [
        receipt
        for receipt in store.receipts
        if start <= receipt.purchase_date < end
    ]


def _build_owner_spend(
    receipts: list[ReceiptRecord],
    *,
    owner_names: dict[str, str],
) -> list[OwnerSpendRow]:
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for receipt in receipts:
        for item in receipt.items:
            totals[item.owner_id] += item.total_price

    grand_total = sum(totals.values(), start=Decimal("0"))
    rows = [
        OwnerSpendRow(
            owner_id=owner_id,
            owner_name=owner_names.get(owner_id, owner_id),
            total_spend=total,
            share_percent=((total / grand_total) * Decimal("100")) if grand_total else None,
        )
        for owner_id, total in totals.items()
    ]
    return sorted(rows, key=lambda row: row.total_spend, reverse=True)


def _build_category_spend(receipts: list[ReceiptRecord]) -> list[CategorySpendRow]:
    totals: dict[ItemCategory, Decimal] = defaultdict(lambda: Decimal("0"))
    for receipt in receipts:
        for item in receipt.items:
            totals[item.category] += item.total_price

    grand_total = sum(totals.values(), start=Decimal("0"))
    rows = [
        CategorySpendRow(
            category=category.value,
            total_spend=total,
            share_percent=((total / grand_total) * Decimal("100")) if grand_total else None,
        )
        for category, total in totals.items()
    ]
    return sorted(rows, key=lambda row: row.total_spend, reverse=True)


def _build_price_change_rows(store: ReceiptStore, year: int, month: int) -> tuple[list[PriceChangeRow], list[PriceChangeRow]]:
    start, end = _month_bounds(year, month)
    month_items: list[tuple[ReceiptRecord, ReceiptItemRecord]] = []
    historical_items: list[tuple[ReceiptRecord, ReceiptItemRecord]] = []

    for receipt in store.receipts:
        for item in receipt.items:
            if not _is_price_ranking_item(item):
                continue
            pair = (receipt, item)
            if start <= receipt.purchase_date < end:
                month_items.append(pair)
            elif receipt.purchase_date < start:
                historical_items.append(pair)

    previous_by_name: dict[str, tuple[ReceiptRecord, ReceiptItemRecord]] = {}
    for receipt, item in sorted(historical_items, key=lambda pair: pair[0].purchase_date):
        previous_by_name[item.normalized_name] = (receipt, item)

    changes: list[PriceChangeRow] = []
    for receipt, item in sorted(month_items, key=lambda pair: pair[0].purchase_date):
        previous = previous_by_name.get(item.normalized_name)
        if previous is None:
            previous_by_name[item.normalized_name] = (receipt, item)
            continue

        previous_receipt, previous_item = previous
        change_amount = item.unit_price - previous_item.unit_price
        if change_amount == 0:
            previous_by_name[item.normalized_name] = (receipt, item)
            continue

        changes.append(
            PriceChangeRow(
                normalized_name=item.normalized_name,
                display_name=item.name,
                previous_unit_price=previous_item.unit_price,
                current_unit_price=item.unit_price,
                change_amount=change_amount,
                change_percent=_safe_percent_change(item.unit_price, previous_item.unit_price),
                latest_purchase_date=receipt.purchase_date,
            )
        )
        previous_by_name[item.normalized_name] = (receipt, item)

    increases = sorted(
        (row for row in changes if row.change_amount > 0),
        key=lambda row: row.change_amount,
        reverse=True,
    )
    decreases = sorted(
        (row for row in changes if row.change_amount < 0),
        key=lambda row: row.change_amount,
    )
    return increases[:5], decreases[:5]


def _build_highlights(report: MonthlyReport) -> ReportHighlights:
    summary: list[str] = []
    summary.append(f"本月共记录 {report.meta.receipt_count} 张小票，正式商品 {report.meta.item_count} 项。")
    if report.overview.top_category:
        summary.append(
            f"支出最高品类是 {report.overview.top_category}，月总支出 {report.category_spend[0].total_spend:.2f} EUR。"
        )
    if report.price_increases:
        top_increase = report.price_increases[0]
        summary.append(
            f"涨价最明显的商品是 {top_increase.display_name}，单价变化 {top_increase.change_amount:.2f} EUR。"
        )
    if report.price_decreases:
        top_decrease = report.price_decreases[0]
        summary.append(
            f"降价最明显的商品是 {top_decrease.display_name}，单价变化 {top_decrease.change_amount:.2f} EUR。"
        )
    return ReportHighlights(summary=summary)


def build_monthly_report(
    store: ReceiptStore,
    *,
    year: int,
    month: int,
    owners_path: str | Path | None = "owners.json",
) -> MonthlyReport:
    owner_names, me_owner_id = _load_owners_map(owners_path)
    month_receipts = _month_receipts(store, year, month)
    quarter_receipts = _quarter_receipts(store, year, month)
    previous_month_year = year - 1 if month == 1 else year
    previous_month = 12 if month == 1 else month - 1
    previous_month_receipts = _month_receipts(store, previous_month_year, previous_month)
    year_ago_receipts = _month_receipts(store, year - 1, month)

    month_total = _sum_receipts(month_receipts)
    quarter_total = _sum_receipts(quarter_receipts)
    owner_spend = _build_owner_spend(month_receipts, owner_names=owner_names)
    category_spend = _build_category_spend(month_receipts)
    price_increases, price_decreases = _build_price_change_rows(store, year, month)
    my_month_total = next((row.total_spend for row in owner_spend if row.owner_id == me_owner_id), None)

    # --- owner-level MoM / YoY (PRD 10.2) ---
    prev_owner_totals = _sum_owner_totals(previous_month_receipts)
    yoy_owner_totals = _sum_owner_totals(year_ago_receipts)
    for row in owner_spend:
        prev_total = prev_owner_totals.get(row.owner_id, Decimal("0"))
        yoy_total = yoy_owner_totals.get(row.owner_id, Decimal("0"))
        row.month_over_month_change = _safe_percent_change(row.total_spend, prev_total)
        row.year_over_year_change = _safe_percent_change(row.total_spend, yoy_total)

    report = MonthlyReport(
        meta=ReportMeta(
            report_month=_format_month(year, month),
            quarter=f"{year:04d}-Q{((_quarter_start_month(month) - 1) // 3) + 1}",
            generated_at=datetime.now(timezone.utc),
            receipt_count=len(month_receipts),
            item_count=sum(len(receipt.items) for receipt in month_receipts),
        ),
        overview=ReportOverview(
            month_total_spend=month_total,
            quarter_total_spend=quarter_total,
            month_over_month_change=_safe_percent_change(month_total, _sum_receipts(previous_month_receipts)),
            year_over_year_change=_safe_percent_change(month_total, _sum_receipts(year_ago_receipts)),
            my_month_total_spend=my_month_total,
            top_category=category_spend[0].category if category_spend else None,
        ),
        owner_spend=owner_spend,
        category_spend=category_spend,
        price_increases=price_increases,
        price_decreases=price_decreases,
        data_quality=ReportDataQuality(
            failed_ocr_count=len(store.failed_ocr_records),
            pending_review_count=sum(
                1
                for receipt in store.receipts
                if receipt.ocr_status.value in {"needs_review", "failed", "pending"}
            ),
            removed_items_count=sum(len(receipt.removed_items) for receipt in month_receipts),
        ),
    )
    report.highlights = _build_highlights(report)
    return report


def _render_money(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f} EUR"


def _render_percent(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.1f}%"


def render_monthly_report_html(report: MonthlyReport) -> str:
    report_json = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)

    def rows_to_html(rows: Iterable[tuple[str, str]]) -> str:
        return "".join(
            f"<div class='row'><span>{label}</span><strong>{value}</strong></div>"
            for label, value in rows
        )

    owner_rows = "".join(
        "<div class='list-row'>"
        f"<span>{row.owner_name}</span>"
        f"<strong>{_render_money(row.total_spend)}</strong>"
        f"<em>{_render_percent(row.share_percent)}</em>"
        f"<small>环比 {_render_percent(row.month_over_month_change)} / 同比 {_render_percent(row.year_over_year_change)}</small>"
        "</div>"
        for row in report.owner_spend
    ) or "<p class='empty'>本月暂无正式消费记录。</p>"

    category_rows = "".join(
        "<div class='list-row'>"
        f"<span>{row.category}</span>"
        f"<strong>{_render_money(row.total_spend)}</strong>"
        f"<em>{_render_percent(row.share_percent)}</em>"
        "</div>"
        for row in report.category_spend
    ) or "<p class='empty'>本月暂无品类支出。</p>"

    def price_rows(rows: list[PriceChangeRow]) -> str:
        return "".join(
            "<div class='list-row price-change'>"
            f"<span>{row.display_name}</span>"
            f"<strong>{row.change_amount:+.2f} EUR</strong>"
            f"<em>{row.previous_unit_price:.2f} -> {row.current_unit_price:.2f}</em>"
            "</div>"
            for row in rows
        ) or "<p class='empty'>暂无数据。</p>"

    highlight_items = "".join(f"<li>{item}</li>" for item in report.highlights.summary)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Expense Report {report.meta.report_month}</title>
  <style>
    :root {{
      --bg: #f5f1e8;
      --card: #fffdf8;
      --ink: #1f2a2e;
      --muted: #6b746f;
      --accent: #c96f3b;
      --accent-soft: #f2d4bf;
      --line: #e6ddd1;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", "Helvetica Neue", sans-serif;
      background:
        radial-gradient(circle at top right, #f9dbc4, transparent 28%),
        linear-gradient(180deg, #f9f4ec 0%, var(--bg) 100%);
      color: var(--ink);
    }}
    .page {{
      max-width: 760px;
      margin: 0 auto;
      padding: 20px 14px 40px;
    }}
    .hero {{
      padding: 18px;
      border-radius: 24px;
      background: linear-gradient(135deg, #fff7ef 0%, #fffdf9 100%);
      border: 1px solid var(--line);
      box-shadow: 0 12px 30px rgba(110, 82, 57, 0.08);
    }}
    .eyebrow {{ color: var(--muted); font-size: 13px; }}
    h1 {{ margin: 8px 0 6px; font-size: 28px; line-height: 1.1; }}
    .subtitle {{ margin: 0; color: var(--muted); }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-top: 16px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 16px;
      box-shadow: 0 8px 20px rgba(110, 82, 57, 0.06);
    }}
    .metric {{
      font-size: 26px;
      font-weight: 700;
      margin: 8px 0 4px;
    }}
    .label {{ color: var(--muted); font-size: 13px; }}
    section {{ margin-top: 14px; }}
    h2 {{ margin: 0 0 10px; font-size: 18px; }}
    .row, .list-row {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 10px;
      padding: 10px 0;
      border-bottom: 1px solid var(--line);
    }}
    .row:last-child, .list-row:last-child {{ border-bottom: 0; }}
    .list-row {{
      display: grid;
      grid-template-columns: 1fr auto auto;
      gap: 10px;
    }}
    strong {{ font-weight: 700; }}
    em {{ font-style: normal; color: var(--muted); font-size: 13px; }}
    ul {{ margin: 0; padding-left: 18px; color: var(--ink); }}
    .empty {{ margin: 0; color: var(--muted); }}
    .footnote {{
      margin-top: 14px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }}
    @media (max-width: 560px) {{
      .grid {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 24px; }}
      .metric {{ font-size: 22px; }}
      .list-row {{ grid-template-columns: 1fr auto; }}
      .price-change em {{ grid-column: 1 / -1; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <section class="hero">
      <div class="eyebrow">Expense Tracker Monthly Report</div>
      <h1>{report.meta.report_month}</h1>
      <p class="subtitle">适合手机查看的单页月报，JSON 与 HTML 同步生成。</p>
      <div class="grid">
        <article class="card">
          <div class="label">本月总支出</div>
          <div class="metric">{_render_money(report.overview.month_total_spend)}</div>
          <div class="label">环比 {_render_percent(report.overview.month_over_month_change)}</div>
        </article>
        <article class="card">
          <div class="label">季度总支出</div>
          <div class="metric">{_render_money(report.overview.quarter_total_spend)}</div>
          <div class="label">同比 {_render_percent(report.overview.year_over_year_change)}</div>
        </article>
        <article class="card">
          <div class="label">我的支出</div>
          <div class="metric">{_render_money(report.overview.my_month_total_spend)}</div>
          <div class="label">正式小票 {report.meta.receipt_count} 张</div>
        </article>
        <article class="card">
          <div class="label">最高品类</div>
          <div class="metric">{report.overview.top_category or "-"}</div>
          <div class="label">正式商品 {report.meta.item_count} 项</div>
        </article>
      </div>
    </section>

    <section class="grid">
      <article class="card">
        <h2>Owner Spend</h2>
        {owner_rows}
      </article>
      <article class="card">
        <h2>Category Spend</h2>
        {category_rows}
      </article>
    </section>

    <section class="grid">
      <article class="card">
        <h2>Price Increases</h2>
        {price_rows(report.price_increases)}
      </article>
      <article class="card">
        <h2>Price Decreases</h2>
        {price_rows(report.price_decreases)}
      </article>
    </section>

    <section class="grid">
      <article class="card">
        <h2>Highlights</h2>
        <ul>{highlight_items}</ul>
      </article>
      <article class="card">
        <h2>Data Quality</h2>
        {rows_to_html([
          ("失败 OCR 记录", str(report.data_quality.failed_ocr_count)),
          ("待复核记录", str(report.data_quality.pending_review_count)),
          ("移除审计项", str(report.data_quality.removed_items_count)),
        ])}
      </article>
    </section>

    <p class="footnote">
      价格排行默认排除 DINING、取消项、负数项和 Leergut/Pfand。月度支出基于正式 formal items 统计。
    </p>
  </main>
  <script type="application/json" id="report-data">{report_json}</script>
</body>
</html>"""


def export_monthly_report_json_schema(
    *,
    output_dir: str | Path = DEFAULT_REPORTS_DIR,
) -> Path:
    schema_dir = Path(output_dir) / "_schema"
    schema_dir.mkdir(parents=True, exist_ok=True)
    schema_path = schema_dir / "monthly_report.schema.json"
    schema = MonthlyReport.model_json_schema()
    schema["title"] = "Expense Tracker Monthly Report"
    schema["$id"] = f"{MONTHLY_REPORT_SCHEMA_NAME}/{MONTHLY_REPORT_SCHEMA_VERSION}"
    schema["x-schema-name"] = MONTHLY_REPORT_SCHEMA_NAME
    schema["x-schema-version"] = MONTHLY_REPORT_SCHEMA_VERSION
    schema_path.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return schema_path


def validate_monthly_report_payload(payload: dict) -> MonthlyReport:
    return MonthlyReport.model_validate(payload)


def write_monthly_report(
    report: MonthlyReport,
    *,
    output_dir: str | Path = DEFAULT_REPORTS_DIR,
    write_schema: bool = False,
) -> WrittenMonthlyReport:
    base_dir = Path(output_dir) / report.meta.report_month
    base_dir.mkdir(parents=True, exist_ok=True)

    json_path = base_dir / "report.json"
    html_path = base_dir / "report.html"

    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    html_path.write_text(render_monthly_report_html(report), encoding="utf-8")
    schema_path = export_monthly_report_json_schema(output_dir=output_dir) if write_schema else None

    return WrittenMonthlyReport(
        report=report,
        json_path=json_path,
        html_path=html_path,
        schema_path=schema_path,
    )


def update_monthly_report(
    *,
    year: int,
    month: int,
    store: ReceiptStore | None = None,
    store_path: str | Path = DEFAULT_STORE_PATH,
    owners_path: str | Path | None = "owners.json",
    output_dir: str | Path = DEFAULT_REPORTS_DIR,
    write_schema: bool = False,
) -> WrittenMonthlyReport:
    resolved_store = store if store is not None else load_receipt_store(store_path)
    report = build_monthly_report(
        resolved_store,
        year=year,
        month=month,
        owners_path=owners_path,
    )
    return write_monthly_report(report, output_dir=output_dir, write_schema=write_schema)
