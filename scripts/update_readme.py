#!/usr/bin/env python3
"""
从 data/transactions.csv 读取冷钱包提现记录，
从 data/holdings.csv 读取全部持仓快照，
更新 README.md 中的进度、持仓表与图表。

用法（在项目根目录执行）:
  python scripts/update_readme.py
"""

from __future__ import annotations

import csv
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "transactions.csv"
HOLDINGS_CSV_PATH = ROOT / "data" / "holdings.csv"
README_PATH = ROOT / "README.md"
CHART_SVG_PATH = ROOT / "assets" / "cumulative_btc.svg"
GOAL_BTC = 0.1
# 图表 0 起点：首次购买比特币的日期（作图用，不计入提现明细）
CHART_ORIGIN_DATE = "2026-03-27"
# 图表横轴最右端：计划达成 0.1 BTC 的目标日期（仅作轴端，不绘制数据点）
CHART_TARGET_DATE = "2029-06-01"

MARKER_START = "<!-- AUTO-GENERATED:START -->"
MARKER_END = "<!-- AUTO-GENERATED:END -->"

# holdings.csv 中 location 字段的展示名
LOCATION_LABELS: dict[str, str] = {
    "cold": "冷钱包",
    "exchange": "交易所",
    "hot": "热钱包",
    "other": "其他",
}

# 表格中位置的展示顺序
LOCATION_ORDER = ("cold", "exchange", "hot", "other")


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    """读取 CSV，跳过空行与 # 注释行，返回原始字段 dict 列表。"""
    if not path.exists():
        return []

    with path.open(encoding="utf-8-sig", newline="") as f:
        lines = []
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            lines.append(line)

    if not lines:
        return []

    return list(csv.DictReader(lines))


def load_transactions(path: Path) -> list[dict]:
    rows: list[dict] = []
    for raw in _read_csv_rows(path):
        date_s = (raw.get("date") or "").strip()
        btc_s = (raw.get("btc") or "").strip()
        if not date_s or not btc_s:
            continue
        try:
            btc = float(btc_s)
        except ValueError:
            continue
        fiat_s = (raw.get("fiat_amount") or "").strip()
        try:
            fiat = float(fiat_s) if fiat_s else None
        except ValueError:
            fiat = None
        rows.append(
            {
                "date": date_s,
                "btc": btc,
                "fiat_amount": fiat,
                "fiat_currency": (raw.get("fiat_currency") or "").strip() or "—",
                "note": (raw.get("note") or "").strip() or "—",
            }
        )

    rows.sort(key=lambda r: r["date"])
    return rows


def load_holdings(path: Path) -> list[dict]:
    """加载持仓快照。同一 location 多条时，取 date 最新的一条。"""
    by_location: dict[str, dict] = {}
    for raw in _read_csv_rows(path):
        date_s = (raw.get("date") or "").strip()
        loc = (raw.get("location") or "").strip().lower()
        btc_s = (raw.get("btc") or "").strip()
        if not date_s or not loc or not btc_s:
            continue
        try:
            btc = float(btc_s)
        except ValueError:
            continue
        row = {
            "date": date_s,
            "location": loc,
            "btc": btc,
            "note": (raw.get("note") or "").strip() or "—",
        }
        prev = by_location.get(loc)
        if prev is None or date_s >= prev["date"]:
            by_location[loc] = row

    def sort_key(r: dict) -> tuple:
        loc = r["location"]
        try:
            idx = LOCATION_ORDER.index(loc)
        except ValueError:
            idx = len(LOCATION_ORDER)
        return (idx, loc)

    return sorted(by_location.values(), key=sort_key)


def load_holdings_series(path: Path) -> list[tuple[str, float]]:
    """按日期汇总全部持仓时序。

    同一日期更新若干 location；未出现在当日的 location 沿用上一次余额（carry-forward）。
    返回 [(date_str, total_btc), ...]，按日期升序，每个日期一点。
    """
    raw_rows: list[tuple[str, str, float]] = []
    for raw in _read_csv_rows(path):
        date_s = (raw.get("date") or "").strip()
        loc = (raw.get("location") or "").strip().lower()
        btc_s = (raw.get("btc") or "").strip()
        if not date_s or not loc or not btc_s:
            continue
        try:
            btc = float(btc_s)
        except ValueError:
            continue
        raw_rows.append((date_s, loc, btc))

    if not raw_rows:
        return []

    raw_rows.sort(key=lambda r: (r[0], r[1]))
    balances: dict[str, float] = {}
    series: list[tuple[str, float]] = []
    i = 0
    n = len(raw_rows)
    while i < n:
        d = raw_rows[i][0]
        while i < n and raw_rows[i][0] == d:
            _, loc, btc = raw_rows[i]
            balances[loc] = btc
            i += 1
        series.append((d, sum(balances.values())))
    return series


def progress_bar(ratio: float, width: int = 20) -> str:
    ratio = max(0.0, min(1.0, ratio))
    filled = int(round(ratio * width))
    return "█" * filled + "░" * (width - filled)


def format_btc(value: float) -> str:
    # 保留足够精度，去掉多余尾零
    s = f"{value:.8f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _parse_date(date_s: str) -> date:
    return datetime.strptime(date_s, "%Y-%m-%d").date()


def _short_date(date_s: str) -> str:
    try:
        return _parse_date(date_s).strftime("%y-%m-%d")
    except ValueError:
        return date_s


def _fmt_tick(d: date) -> str:
    return d.strftime("%y-%m-%d")


COLOR_COLD = "#2563eb"  # 蓝：冷钱包累计
COLOR_TOTAL = "#ea580c"  # 橙：全部持仓
COLOR_DCA = "#9ca3af"  # 灰虚线：定投参考（起点 0 → 目标日 0.1）


def _series_to_points(
    origin: date,
    series: list[tuple[date, float]],
) -> list[tuple[date, float]]:
    """在 series 前补起点 (origin, 0)；同日保留最后值。"""
    points: list[tuple[date, float]] = [(origin, 0.0)]
    for d, v in series:
        if d < origin:
            continue
        if points and points[-1][0] == d:
            points[-1] = (d, v)
        else:
            points.append((d, v))
    return points


def _path_d(
    points: list[tuple[date, float]],
    x_of,
    y_of,
) -> str:
    if len(points) < 2:
        return ""
    parts: list[str] = []
    for i, (d, v) in enumerate(points):
        cmd = "M" if i == 0 else "L"
        parts.append(f"{cmd}{x_of(d):.2f},{y_of(v):.2f}")
    return " ".join(parts)


def _point_elems(
    points: list[tuple[date, float]],
    x_of,
    y_of,
    fill: str,
) -> list[str]:
    elems: list[str] = []
    for d, v in points:
        elems.append(
            f'<circle cx="{x_of(d):.2f}" cy="{y_of(v):.2f}" r="3.5" '
            f'fill="{fill}" stroke="#ffffff" stroke-width="1.5"/>'
        )
    return elems


def write_chart_svg(
    transactions: list[dict],
    cumulative: list[float],
    holdings_series: list[tuple[str, float]],
    path: Path,
) -> None:
    """按真实时间比例绘制累计折线 SVG。

    - 横轴：CHART_ORIGIN_DATE → CHART_TARGET_DATE（线性时间）
    - 目标日仅作为轴右端，不绘制任何数据点/线终点
    - 纵轴固定 0 → GOAL_BTC（若数据更高则上扩）
    - 蓝线：冷钱包累计；橙线：全部持仓（holdings 快照时序）
    - 灰虚线：定投参考（绘图区左下角 → 右上角，线性进度）
    """
    origin = _parse_date(CHART_ORIGIN_DATE)
    target = _parse_date(CHART_TARGET_DATE)
    if target <= origin:
        raise ValueError("CHART_TARGET_DATE must be after CHART_ORIGIN_DATE")

    cold_series: list[tuple[date, float]] = []
    for t, cum in zip(transactions, cumulative):
        cold_series.append((_parse_date(t["date"]), cum))

    total_series: list[tuple[date, float]] = []
    for date_s, total in holdings_series:
        total_series.append((_parse_date(date_s), total))

    cold_points = _series_to_points(origin, cold_series)
    total_points = _series_to_points(origin, total_series)

    y_max = GOAL_BTC
    data_vals = [v for _, v in cold_points] + [v for _, v in total_points]
    if data_vals:
        data_max = max(data_vals)
        if data_max > y_max:
            y_max = data_max * 1.05

    axis_end = target
    for pts in (cold_points, total_points):
        if pts:
            axis_end = max(axis_end, pts[-1][0])

    total_days = (axis_end - origin).days
    if total_days <= 0:
        total_days = 1

    # 画布与边距（略增顶边放图例）
    width, height = 920, 460
    margin_left, margin_right = 64, 28
    margin_top, margin_bottom = 56, 56
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    def x_of(d: date) -> float:
        return margin_left + ((d - origin).days / total_days) * plot_w

    def y_of(v: float) -> float:
        v = max(0.0, min(v, y_max))
        return margin_top + plot_h * (1.0 - v / y_max)

    cold_path = _path_d(cold_points, x_of, y_of)
    total_path = _path_d(total_points, x_of, y_of)

    # 定投参考：绘图区左下角 (0,0) → 右上角 (axis_end, y_max)
    # 在默认域（起点→目标日、0→0.1）下即理想线性进度线
    dca_x1 = margin_left
    dca_y1 = margin_top + plot_h
    dca_x2 = margin_left + plot_w
    dca_y2 = margin_top
    dca_line = (
        f'<line x1="{dca_x1:.2f}" y1="{dca_y1:.2f}" '
        f'x2="{dca_x2:.2f}" y2="{dca_y2:.2f}" '
        f'stroke="{COLOR_DCA}" stroke-width="1.75" stroke-dasharray="7 5" '
        f'stroke-linecap="round"/>'
    )

    # Y 轴刻度
    y_ticks = 5
    y_tick_elems: list[str] = []
    for i in range(y_ticks + 1):
        val = y_max * i / y_ticks
        y = y_of(val)
        label = f"{val:.3f}".rstrip("0").rstrip(".")
        y_tick_elems.append(
            f'<line x1="{margin_left}" y1="{y:.2f}" '
            f'x2="{margin_left + plot_w}" y2="{y:.2f}" '
            f'stroke="#e5e7eb" stroke-width="1"/>'
            f'<line x1="{margin_left - 5}" y1="{y:.2f}" '
            f'x2="{margin_left}" y2="{y:.2f}" stroke="#6b7280" stroke-width="1"/>'
            f'<text x="{margin_left - 10}" y="{y + 4:.2f}" text-anchor="end" '
            f'font-size="11" fill="#4b5563" font-family="Segoe UI, Helvetica, Arial, sans-serif">'
            f"{escape(label)}</text>"
        )

    # X 轴刻度：起点、若干均匀时间点、目标日（最右端）
    x_tick_dates: list[date] = [origin]
    for i in range(1, 5):
        d = origin + timedelta(days=round(total_days * i / 5))
        if origin < d < axis_end:
            x_tick_dates.append(d)
    if axis_end not in x_tick_dates:
        x_tick_dates.append(axis_end)
    x_tick_dates = sorted(set(x_tick_dates))

    x_tick_elems: list[str] = []
    for d in x_tick_dates:
        x = x_of(d)
        x_tick_elems.append(
            f'<line x1="{x:.2f}" y1="{margin_top + plot_h}" '
            f'x2="{x:.2f}" y2="{margin_top + plot_h + 5}" '
            f'stroke="#6b7280" stroke-width="1"/>'
            f'<text x="{x:.2f}" y="{margin_top + plot_h + 22}" text-anchor="middle" '
            f'font-size="11" fill="#4b5563" font-family="Segoe UI, Helvetica, Arial, sans-serif">'
            f"{escape(_fmt_tick(d))}</text>"
        )

    cold_dots = _point_elems(cold_points, x_of, y_of, COLOR_COLD)
    total_dots = _point_elems(total_points, x_of, y_of, COLOR_TOTAL)

    # 图例（右上，三项）
    legend_x = margin_left + plot_w - 168
    legend_y = margin_top + 10
    legend = (
        f'<rect x="{legend_x - 8:.1f}" y="{legend_y - 4:.1f}" width="176" height="58" '
        f'rx="4" fill="#ffffff" fill-opacity="0.92" stroke="#e5e7eb"/>'
        f'<line x1="{legend_x:.1f}" y1="{legend_y + 8:.1f}" '
        f'x2="{legend_x + 22:.1f}" y2="{legend_y + 8:.1f}" '
        f'stroke="{COLOR_COLD}" stroke-width="2.5"/>'
        f'<circle cx="{legend_x + 11:.1f}" cy="{legend_y + 8:.1f}" r="3" fill="{COLOR_COLD}"/>'
        f'<text x="{legend_x + 28:.1f}" y="{legend_y + 12:.1f}" font-size="12" fill="#374151" '
        f'font-family="Segoe UI, Helvetica, Arial, sans-serif">冷钱包累计</text>'
        f'<line x1="{legend_x:.1f}" y1="{legend_y + 26:.1f}" '
        f'x2="{legend_x + 22:.1f}" y2="{legend_y + 26:.1f}" '
        f'stroke="{COLOR_TOTAL}" stroke-width="2.5"/>'
        f'<circle cx="{legend_x + 11:.1f}" cy="{legend_y + 26:.1f}" r="3" fill="{COLOR_TOTAL}"/>'
        f'<text x="{legend_x + 28:.1f}" y="{legend_y + 30:.1f}" font-size="12" fill="#374151" '
        f'font-family="Segoe UI, Helvetica, Arial, sans-serif">全部持仓</text>'
        f'<line x1="{legend_x:.1f}" y1="{legend_y + 44:.1f}" '
        f'x2="{legend_x + 22:.1f}" y2="{legend_y + 44:.1f}" '
        f'stroke="{COLOR_DCA}" stroke-width="1.75" stroke-dasharray="5 3"/>'
        f'<text x="{legend_x + 28:.1f}" y="{legend_y + 48:.1f}" font-size="12" fill="#374151" '
        f'font-family="Segoe UI, Helvetica, Arial, sans-serif">定投参考</text>'
    )

    title = "BTC cumulative: cold wallet & total holdings"
    cold_line = (
        f'<path d="{cold_path}" fill="none" stroke="{COLOR_COLD}" stroke-width="2.5" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        if cold_path
        else ""
    )
    total_line = (
        f'<path d="{total_path}" fill="none" stroke="{COLOR_TOTAL}" stroke-width="2.5" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        if total_path
        else ""
    )

    svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">
  <rect width="100%" height="100%" fill="#ffffff"/>
  <text x="{width / 2:.1f}" y="28" text-anchor="middle" font-size="16" font-weight="600"
        fill="#111827" font-family="Segoe UI, Helvetica, Arial, sans-serif">{escape(title)}</text>
  <!-- grid + y ticks -->
  {"".join(y_tick_elems)}
  <!-- axes -->
  <line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_h}"
        stroke="#374151" stroke-width="1.5"/>
  <line x1="{margin_left}" y1="{margin_top + plot_h}" x2="{margin_left + plot_w}" y2="{margin_top + plot_h}"
        stroke="#374151" stroke-width="1.5"/>
  <!-- x ticks -->
  {"".join(x_tick_elems)}
  <!-- y axis title -->
  <text x="16" y="{margin_top + plot_h / 2:.1f}" text-anchor="middle" font-size="12" fill="#374151"
        font-family="Segoe UI, Helvetica, Arial, sans-serif"
        transform="rotate(-90 16 {margin_top + plot_h / 2:.1f})">BTC</text>
  <!-- DCA reference diagonal (under data series) -->
  {dca_line}
  <!-- total holdings (orange) under cold so cold stays visible when equal -->
  {total_line}
  {"".join(total_dots)}
  <!-- cold wallet (blue) -->
  {cold_line}
  {"".join(cold_dots)}
  <!-- legend -->
  {legend}
</svg>
'''
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")


def chart_markdown(
    transactions: list[dict],
    cumulative: list[float],
    holdings_series: list[tuple[str, float]],
) -> str:
    """写入 SVG 并返回 README 中引用图表的 Markdown。"""
    write_chart_svg(transactions, cumulative, holdings_series, CHART_SVG_PATH)
    cold_total = cumulative[-1] if cumulative else 0.0
    holdings_total = holdings_series[-1][1] if holdings_series else 0.0
    cache_bust = (
        f"{len(transactions)}-{format_btc(cold_total)}-"
        f"{len(holdings_series)}-{format_btc(holdings_total)}-{CHART_TARGET_DATE}"
    )
    rel = CHART_SVG_PATH.relative_to(ROOT).as_posix()
    note = (
        f"\n\n_起点为首次购买日 `{CHART_ORIGIN_DATE}`（累计 0）；"
        f"**蓝线**为冷钱包提现累计，**橙线**为全部持仓（`holdings.csv` 快照时序）；"
        f"**灰虚线**连接左下角与右上角，为定投参考（线性进度）；"
        f"横轴按真实时间比例，最右端为目标日 `{CHART_TARGET_DATE}`（**不绘制**数据点）；"
        f"纵轴默认 0 → {GOAL_BTC} BTC。_"
    )
    if not transactions and not holdings_series:
        note = (
            f"\n\n_暂无数据。图表起点为首次购买日 `{CHART_ORIGIN_DATE}`，"
            f"横轴最右端为目标日 `{CHART_TARGET_DATE}`（仅作轴端）。_"
        )
    return (
        f'![BTC cumulative cold wallet and total holdings]({rel}?v={cache_bust})\n'
        f"{note}"
    )


def build_table(transactions: list[dict], cumulative: list[float]) -> str:
    if not transactions:
        return "_暂无记录。请在 `data/transactions.csv` 中添加提现到冷钱包的记录。_"

    lines = [
        "| 日期 | 提现 (BTC) | 累计 (BTC) | 成本 | 均价 | 备注 |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for t, cum in zip(transactions, cumulative):
        if t["fiat_amount"] is not None:
            cur = t["fiat_currency"] if t["fiat_currency"] != "—" else ""
            fiat = f"{t['fiat_amount']:,.2f} {cur}".strip()
            if t["btc"] > 0:
                unit = t["fiat_amount"] / t["btc"]
                avg = f"{unit:,.0f} {cur}/BTC".strip()
            else:
                avg = "—"
        else:
            fiat = "—"
            avg = "—"
        lines.append(
            f"| {t['date']} | {format_btc(t['btc'])} | {format_btc(cum)} | "
            f"{fiat} | {avg} | {t['note']} |"
        )
    return "\n".join(lines)


def build_holdings_table(holdings: list[dict]) -> str:
    """全部持仓表：冷钱包 / 交易所等并列，含合计行。"""
    if not holdings:
        return (
            "_暂无持仓快照。请在 `data/holdings.csv` 中按位置记录当前持仓"
            "（`cold` / `exchange` 等）。_"
        )

    total = sum(h["btc"] for h in holdings)
    # 快照日期：取各行中最新的 date
    as_of = max(h["date"] for h in holdings)

    lines = [
        f"_快照日期：`{as_of}`_",
        "",
        "| 位置 | 持仓 (BTC) | 占比 | 备注 |",
        "| --- | ---: | ---: | --- |",
    ]
    for h in holdings:
        label = LOCATION_LABELS.get(h["location"], h["location"])
        share = (h["btc"] / total * 100) if total > 0 else 0.0
        lines.append(
            f"| {label} | {format_btc(h['btc'])} | {share:.2f}% | {h['note']} |"
        )
    lines.append(
        f"| **合计** | **{format_btc(total)}** | **100%** | 全部持仓 |"
    )
    return "\n".join(lines)


def build_auto_section(
    transactions: list[dict],
    holdings: list[dict],
    holdings_series: list[tuple[str, float]],
) -> str:
    cumulative: list[float] = []
    cold_total = 0.0
    total_fiat_by_currency: dict[str, float] = {}

    for t in transactions:
        cold_total += t["btc"]
        cumulative.append(cold_total)
        if t["fiat_amount"] is not None:
            cur = t["fiat_currency"] if t["fiat_currency"] != "—" else "UNKNOWN"
            total_fiat_by_currency[cur] = (
                total_fiat_by_currency.get(cur, 0.0) + t["fiat_amount"]
            )

    holdings_total = sum(h["btc"] for h in holdings) if holdings else 0.0
    # 进度以冷钱包累计为准（目标 0.1 BTC 的囤积进度）
    ratio = cold_total / GOAL_BTC if GOAL_BTC else 0.0
    pct = ratio * 100
    remaining = max(0.0, GOAL_BTC - cold_total)
    bar = progress_bar(ratio)

    fiat_lines = []
    for cur, amount in sorted(total_fiat_by_currency.items()):
        fiat_lines.append(f"- **累计投入 ({cur})**: {amount:,.2f}")
    if not fiat_lines:
        fiat_lines.append("- **累计投入**: —")

    avg_cost_lines = []
    for cur, amount in sorted(total_fiat_by_currency.items()):
        if cold_total > 0:
            avg = amount / cold_total
            # USD 均价按常见报价取整展示（如 $72040）
            if cur.upper() == "USD":
                avg_cost_lines.append(
                    f"- **平均成本 (USD/BTC)**: ${avg:,.0f}"
                )
            else:
                avg_cost_lines.append(
                    f"- **平均成本 ({cur}/BTC)**: {avg:,.2f}"
                )

    holdings_as_of = max((h["date"] for h in holdings), default="—")
    holdings_line = (
        f"- **全部持仓合计**: {format_btc(holdings_total)} BTC"
        f"（快照 `{holdings_as_of}`）"
        if holdings
        else "- **全部持仓合计**: —（见 `data/holdings.csv`）"
    )

    updated = datetime.now().strftime("%Y-%m-%d %H:%M")

    parts = [
        (
            f"> 自动生成于 `{updated}` · 目标 **{GOAL_BTC} BTC** · "
            f"数据源 `data/transactions.csv` + `data/holdings.csv`"
        ),
        "",
        "## 进度总览",
        "",
        f"**{format_btc(cold_total)} / {GOAL_BTC} BTC**  ·  **{pct:.2f}%**",
        "",
        f"`{bar}`",
        "",
        f"- **冷钱包累计**: {format_btc(cold_total)} BTC",
        holdings_line,
        f"- **距离目标还差**: {format_btc(remaining)} BTC",
        f"- **提现笔数**: {len(transactions)}",
        *fiat_lines,
        *avg_cost_lines,
        "",
        "## 全部持仓",
        "",
        build_holdings_table(holdings),
        "",
        "## 累计曲线",
        "",
        chart_markdown(transactions, cumulative, holdings_series),
        "",
        "## 提现明细",
        "",
        build_table(transactions, cumulative),
        "",
    ]
    return "\n".join(parts)


def update_readme(readme_path: Path, auto_body: str) -> None:
    block = f"{MARKER_START}\n{auto_body.rstrip()}\n{MARKER_END}"

    if readme_path.exists():
        text = readme_path.read_text(encoding="utf-8")
    else:
        text = ""

    pattern = re.compile(
        re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END),
        re.DOTALL,
    )

    if pattern.search(text):
        new_text = pattern.sub(block, text)
    else:
        # 若尚无标记，追加到文件末尾
        if text and not text.endswith("\n"):
            text += "\n"
        new_text = text + "\n" + block + "\n"

    readme_path.write_text(new_text, encoding="utf-8")


def main() -> None:
    txs = load_transactions(CSV_PATH)
    holdings = load_holdings(HOLDINGS_CSV_PATH)
    holdings_series = load_holdings_series(HOLDINGS_CSV_PATH)
    auto = build_auto_section(txs, holdings, holdings_series)
    update_readme(README_PATH, auto)
    cold_total = sum(t["btc"] for t in txs)
    holdings_total = sum(h["btc"] for h in holdings)
    print(
        f"已更新 README.md：冷钱包 {len(txs)} 笔 / {format_btc(cold_total)} BTC；"
        f"持仓 {len(holdings)} 处 / 合计 {format_btc(holdings_total)} BTC"
    )


if __name__ == "__main__":
    main()
