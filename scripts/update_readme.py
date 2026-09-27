#!/usr/bin/env python3
"""
从 data/transactions.csv 读取冷钱包提现记录，
从 data/holdings.csv 读取全部持仓快照，
抓取 BTC/USD、BTC/CNY 实时价格生成「当前市值」，
并把每日市值写入 data/prices.csv，
更新 README.md 中的进度、持仓表与图表。

用法（在项目根目录执行）:
  python scripts/update_readme.py
"""

from __future__ import annotations

import csv
import json
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "transactions.csv"
HOLDINGS_CSV_PATH = ROOT / "data" / "holdings.csv"
PRICES_CSV_PATH = ROOT / "data" / "prices.csv"
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
    "binance": "Binance",
    "okx": "OKX",
    "hot": "热钱包",
    "other": "其他",
}

# 表格中位置的展示顺序
LOCATION_ORDER = ("binance", "okx", "hot", "other")


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
    """加载所有持仓快照，按日期升序、位置顺序排列。"""
    rows: list[dict] = []
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
        rows.append({
            "date": date_s,
            "location": loc,
            "btc": btc,
            "note": (raw.get("note") or "").strip() or "—",
        })

    def sort_key(r: dict) -> tuple:
        loc = r["location"]
        try:
            idx = LOCATION_ORDER.index(loc)
        except ValueError:
            idx = len(LOCATION_ORDER)
        return (r["date"], idx, loc)

    return sorted(rows, key=sort_key)


def load_holdings_series(path: Path) -> list[tuple[str, float]]:
    """按日期汇总全部持仓时序。

    同一日期更新若干 location；未出现在当日的 location 清零（不 carry-forward）。
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
    series: list[tuple[str, float]] = []
    i = 0
    n = len(raw_rows)
    while i < n:
        d = raw_rows[i][0]
        day_total = 0.0
        while i < n and raw_rows[i][0] == d:
            _, _, btc = raw_rows[i]
            day_total += btc
            i += 1
        series.append((d, day_total))
    return series


def progress_bar(ratio: float, width: int = 20) -> str:
    ratio = max(0.0, min(1.0, ratio))
    filled = int(round(ratio * width))
    return "█" * filled + "░" * (width - filled)


def format_btc(value: float) -> str:
    # 保留足够精度，去掉多余尾零
    s = f"{value:.8f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _fetch_json(url: str, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_usd_cny_rate() -> float | None:
    """获取 USD/CNY 实时汇率（法币源）。"""
    for url in (
        "https://open.er-api.com/v6/latest/USD",
        "https://api.frankfurter.app/latest?from=USD&to=CNY",
    ):
        try:
            data = _fetch_json(url)
            rate = data.get("rates", {}).get("CNY")
            if rate:
                return float(rate)
        except Exception:
            continue
    return None


def _btc_usd_from_exchange() -> float | None:
    """交易所行情降级源：BTC/USDT。"""
    for url in (
        "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT",
        "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
    ):
        try:
            data = _fetch_json(url)
            if "data" in data:  # OKX
                return float(data["data"][0]["last"])
            return float(data["price"])  # Binance
        except Exception:
            continue
    return None


def get_market_rates() -> tuple[float | None, float | None]:
    """获取 (BTC/USD, BTC/CNY)。

    首选 CoinGecko 一次拿两种报价；失败时降级为交易所行情 + 法币汇率。
    """
    btc_usd = btc_cny = None
    try:
        data = _fetch_json(
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=bitcoin&vs_currencies=usd,cny"
        )
        coin = data.get("bitcoin", {})
        btc_usd = float(coin["usd"]) if coin.get("usd") else None
        btc_cny = float(coin["cny"]) if coin.get("cny") else None
    except Exception:
        pass

    if btc_usd is None or btc_cny is None:
        btc_usd = btc_usd or _btc_usd_from_exchange()
        usd_cny = get_usd_cny_rate()
        if btc_usd and usd_cny and btc_cny is None:
            btc_cny = btc_usd * usd_cny

    return btc_usd, btc_cny


def load_prices(path: Path) -> list[dict]:
    """读取每日市值快照，按日期升序返回。"""
    rows: list[dict] = []
    for raw in _read_csv_rows(path):
        date_s = (raw.get("date") or "").strip()
        if not date_s:
            continue
        try:
            btc_usd = float((raw.get("btc_usd") or "").strip())
            btc_cny = float((raw.get("btc_cny") or "").strip())
            value_cny = float((raw.get("value_cny") or "").strip())
        except ValueError:
            continue
        try:
            value_usd = float((raw.get("value_usd") or "").strip())
        except ValueError:
            value_usd = 0.0
        rows.append(
            {
                "date": date_s,
                "btc_usd": btc_usd,
                "btc_cny": btc_cny,
                "value_cny": value_cny,
                "value_usd": value_usd,
            }
        )
    rows.sort(key=lambda r: r["date"])
    return rows


def write_price_snapshot(
    path: Path,
    date_s: str,
    btc_usd: float,
    btc_cny: float,
    value_cny: float,
    value_usd: float,
) -> None:
    """写入当日市值快照（同日覆盖），保持按日期升序。"""
    rows = [r for r in load_prices(path) if r["date"] != date_s]
    rows.append(
        {
            "date": date_s,
            "btc_usd": btc_usd,
            "btc_cny": btc_cny,
            "value_cny": value_cny,
            "value_usd": value_usd,
        }
    )
    rows.sort(key=lambda r: r["date"])

    lines = ["date,btc_usd,btc_cny,value_cny,value_usd"]
    for r in rows:
        lines.append(
            f'{r["date"]},{r["btc_usd"]:.2f},{r["btc_cny"]:.2f},'
            f'{r["value_cny"]:.2f},{r["value_usd"]:.2f}'
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_date(date_s: str) -> date:
    return datetime.strptime(date_s, "%Y-%m-%d").date()


def _short_date(date_s: str) -> str:
    try:
        return _parse_date(date_s).strftime("%y-%m-%d")
    except ValueError:
        return date_s


def _fmt_tick(d: date) -> str:
    return d.strftime("%y-%m-%d")


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
    - 橙线：全部持仓（holdings 快照时序）
    - 灰虚线：定投参考（绘图区左下角 → 右上角，线性进度）
    """
    origin = _parse_date(CHART_ORIGIN_DATE)
    target = _parse_date(CHART_TARGET_DATE)
    if target <= origin:
        raise ValueError("CHART_TARGET_DATE must be after CHART_ORIGIN_DATE")

    total_series: list[tuple[date, float]] = []
    for date_s, total in holdings_series:
        total_series.append((_parse_date(date_s), total))

    total_points = _series_to_points(origin, total_series)

    y_max = GOAL_BTC
    data_vals = [v for _, v in total_points]
    if data_vals:
        data_max = max(data_vals)
        if data_max > y_max:
            y_max = data_max * 1.05

    axis_end = target
    for pts in (total_points,):
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

    total_dots = _point_elems(total_points, x_of, y_of, COLOR_TOTAL)

    # 图例（右上，两项）
    legend_x = margin_left + plot_w - 168
    legend_y = margin_top + 10
    legend = (
        f'<rect x="{legend_x - 8:.1f}" y="{legend_y - 4:.1f}" width="176" height="42" '
        f'rx="4" fill="#ffffff" fill-opacity="0.92" stroke="#e5e7eb"/>'
        f'<line x1="{legend_x:.1f}" y1="{legend_y + 8:.1f}" '
        f'x2="{legend_x + 22:.1f}" y2="{legend_y + 8:.1f}" '
        f'stroke="{COLOR_TOTAL}" stroke-width="2.5"/>'
        f'<circle cx="{legend_x + 11:.1f}" cy="{legend_y + 8:.1f}" r="3" fill="{COLOR_TOTAL}"/>'
        f'<text x="{legend_x + 28:.1f}" y="{legend_y + 12:.1f}" font-size="12" fill="#374151" '
        f'font-family="Segoe UI, Helvetica, Arial, sans-serif">全部持仓</text>'
        f'<line x1="{legend_x:.1f}" y1="{legend_y + 26:.1f}" '
        f'x2="{legend_x + 22:.1f}" y2="{legend_y + 26:.1f}" '
        f'stroke="{COLOR_DCA}" stroke-width="1.75" stroke-dasharray="5 3"/>'
        f'<text x="{legend_x + 28:.1f}" y="{legend_y + 30:.1f}" font-size="12" fill="#374151" '
        f'font-family="Segoe UI, Helvetica, Arial, sans-serif">定投参考</text>'
    )

    title = "BTC total holdings"
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
  <!-- total holdings (orange) -->
  {total_line}
  {"".join(total_dots)}
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
    holdings_total = holdings_series[-1][1] if holdings_series else 0.0
    cache_bust = (
        f"{len(holdings_series)}-{format_btc(holdings_total)}-{CHART_TARGET_DATE}"
    )
    rel = CHART_SVG_PATH.relative_to(ROOT).as_posix()
    note = (
        f"\n\n_起点为首次购买日 `{CHART_ORIGIN_DATE}`（累计 0）；"
        f"**橙线**为全部持仓（`holdings.csv` 快照时序）；"
        f"**灰虚线**连接左下角与右上角，为定投参考（线性进度）；"
        f"横轴按真实时间比例，最右端为目标日 `{CHART_TARGET_DATE}`（**不绘制**数据点）；"
        f"纵轴默认 0 → {GOAL_BTC} BTC。_"
    )
    if not holdings_series:
        note = (
            f"\n\n_暂无数据。图表起点为首次购买日 `{CHART_ORIGIN_DATE}`，"
            f"横轴最右端为目标日 `{CHART_TARGET_DATE}`（仅作轴端）。_"
        )
    return (
        f'![BTC total holdings]({rel}?v={cache_bust})\n'
        f"{note}"
    )


def build_table(transactions: list[dict], cumulative: list[float]) -> str:
    if not transactions:
        return "_暂无提现记录。当前仅使用交易所存储比特币。_"

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


def build_holdings_table(holdings: list[dict], btc_usd: float | None, btc_cny: float | None) -> str:
    """全部持仓表：按日期分组展示历史快照。"""
    if not holdings:
        return (
            "_暂无持仓快照。请在 `data/holdings.csv` 中按位置记录当前持仓。_"
        )

    # 按日期分组
    by_date: dict[str, list[dict]] = {}
    for h in holdings:
        d = h["date"]
        if d not in by_date:
            by_date[d] = []
        by_date[d].append(h)

    lines = [
        "| 日期 | 位置 | 持仓 (BTC) | 备注 |",
        "| --- | --- | ---: | --- |",
    ]

    for date_str in sorted(by_date.keys()):
        day_holdings = by_date[date_str]
        day_total = sum(h["btc"] for h in day_holdings)
        for i, h in enumerate(day_holdings):
            label = LOCATION_LABELS.get(h["location"], h["location"])
            date_col = date_str if i == 0 else ""
            lines.append(
                f"| {date_col} | {label} | {format_btc(h['btc'])} | {h['note']} |"
            )

    # 最新日期的合计
    latest_date = max(by_date.keys())
    latest_total = sum(h["btc"] for h in by_date[latest_date])

    if btc_usd and btc_cny:
        fiat_info = (
            f"≈ ${latest_total * btc_usd:,.2f} / ¥{latest_total * btc_cny:,.2f}"
        )
    else:
        fiat_info = "最新持仓"

    lines.append(
        f"| **合计** | | **{format_btc(latest_total)}** | {fiat_info} |"
    )
    return "\n".join(lines)


def build_market_value_section(
    holdings_total: float,
    btc_usd: float | None,
    btc_cny: float | None,
    holdings_as_of: str,
    price_as_of: str,
    price_live: bool,
) -> list[str]:
    """显眼的「当前市值」区块（人民币为主）。"""
    if not (btc_usd and btc_cny):
        return [
            "## 当前市值",
            "",
            "_暂时无法获取实时价格（网络不可用且无历史快照）。_",
            "",
        ]

    cny_value = holdings_total * btc_cny
    usd_value = holdings_total * btc_usd
    price_note = (
        f"价格更新于 `{price_as_of}`"
        if price_live
        else f"价格取自快照 `{price_as_of}`（实时接口不可用）"
    )
    detail = (
        f"≈ **${usd_value:,.2f}** · 1 BTC = **¥{btc_cny:,.0f}** / ${btc_usd:,.0f} · "
        f"持仓 **{format_btc(holdings_total)} BTC** · "
        f"快照 `{holdings_as_of}` · {price_note}"
    )
    return [
        "## 当前市值",
        "",
        f"### ¥{cny_value:,.2f}",
        "",
        detail,
        "",
    ]


def build_auto_section(
    transactions: list[dict],
    holdings: list[dict],
    holdings_series: list[tuple[str, float]],
    btc_usd: float | None = None,
    btc_cny: float | None = None,
    price_as_of: str = "",
    price_live: bool = True,
) -> str:
    # 只计算最新日期的持仓
    if holdings:
        latest_date = max(h["date"] for h in holdings)
        latest_holdings = [h for h in holdings if h["date"] == latest_date]
    else:
        latest_holdings = []
    holdings_total = sum(h["btc"] for h in latest_holdings)
    # 进度以总持仓为准（目标 0.1 BTC 的囤积进度）
    ratio = holdings_total / GOAL_BTC if GOAL_BTC else 0.0
    pct = ratio * 100
    remaining = max(0.0, GOAL_BTC - holdings_total)
    bar = progress_bar(ratio)

    # 计算各交易所的均价（从最新日期的 holdings.csv 的 note 字段解析）
    avg_cost_lines = []
    total_value_usd = 0.0
    total_btc_with_price = 0.0
    
    for h in latest_holdings:
        note = h.get("note", "")
        if "均价" in note:
            # 提取均价信息
            import re
            match = re.search(r'均价\s*\$?([\d,\.]+)', note)
            if match:
                avg_price = match.group(1).replace(",", "")
                try:
                    avg_price_float = float(avg_price)
                    btc_amount = h["btc"]
                    avg_cost_lines.append(
                        f"- **{LOCATION_LABELS.get(h['location'], h['location'])} 均价**: ${avg_price_float:,.1f}/BTC"
                    )
                    # 计算加权均价
                    total_value_usd += btc_amount * avg_price_float
                    total_btc_with_price += btc_amount
                except ValueError:
                    pass
    
    # 计算全部持仓的加权均价
    if total_btc_with_price > 0:
        overall_avg_price = total_value_usd / total_btc_with_price
        avg_cost_lines.append(f"- **全部持仓均价**: ${overall_avg_price:,.1f}/BTC")

    usd_rate = btc_usd
    cny_rate = btc_cny
    if usd_rate and cny_rate:
        usd_value = holdings_total * usd_rate
        cny_value = holdings_total * cny_rate
        fiat_str = f"（≈ ${usd_value:,.2f} / ¥{cny_value:,.2f}）"
    else:
        fiat_str = ""

    holdings_as_of = max((h["date"] for h in holdings), default="—")

    updated = datetime.now().strftime("%Y-%m-%d %H:%M")

    parts = [
        (
            f"> 自动生成于 `{updated}` · 目标 **{GOAL_BTC} BTC** · "
            f"数据源 `data/holdings.csv`"
        ),
        "",
        *build_market_value_section(
            holdings_total, btc_usd, btc_cny, holdings_as_of, price_as_of, price_live
        ),
        "## 进度总览",
        "",
        f"**{format_btc(holdings_total)} / {GOAL_BTC} BTC**  ·  **{pct:.2f}%**",
        "",
        f"`{bar}`",
        "",
        f"- **全部持仓合计**: {format_btc(holdings_total)} BTC{fiat_str}（快照 `{holdings_as_of}`）",
        f"- **距离目标还差**: {format_btc(remaining)} BTC",
        *avg_cost_lines,
        "",
        "## 全部持仓",
        "",
        build_holdings_table(holdings, btc_usd, btc_cny),
        "",
        "## 累计曲线",
        "",
        chart_markdown(transactions, [], holdings_series),
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
    holdings = load_holdings(HOLDINGS_CSV_PATH)
    holdings_series = load_holdings_series(HOLDINGS_CSV_PATH)

    # 只统计最新快照日期的持仓
    if holdings:
        latest_date = max(h["date"] for h in holdings)
        holdings_total = sum(h["btc"] for h in holdings if h["date"] == latest_date)
    else:
        latest_date = "—"
        holdings_total = 0.0

    btc_usd, btc_cny = get_market_rates()
    price_live = bool(btc_usd and btc_cny)
    price_as_of = date.today().isoformat()

    if not price_live:
        # 网络不可用：回退到最近一次价格快照，避免 README 中市值消失
        snapshots = load_prices(PRICES_CSV_PATH)
        if snapshots:
            btc_usd = btc_usd or snapshots[-1]["btc_usd"]
            btc_cny = btc_cny or snapshots[-1]["btc_cny"]
            price_as_of = snapshots[-1]["date"]
        else:
            print("警告：无法获取实时价格，且没有历史价格快照可用")

    if price_live and btc_usd and btc_cny:
        # 仅实时价格才写入当日快照，避免把过期价格记成当天
        write_price_snapshot(
            PRICES_CSV_PATH,
            price_as_of,
            btc_usd,
            btc_cny,
            holdings_total * btc_cny,
            holdings_total * btc_usd,
        )

    auto = build_auto_section(
        [], holdings, holdings_series, btc_usd, btc_cny, price_as_of, price_live
    )
    update_readme(README_PATH, auto)

    msg = (
        f"已更新 README.md：持仓 {len(holdings)} 处 / "
        f"合计 {format_btc(holdings_total)} BTC"
    )
    if btc_cny:
        msg += f" / 当前市值 ¥{holdings_total * btc_cny:,.2f}"
    print(msg)


if __name__ == "__main__":
    # Windows 控制台默认 GBK，统一按 UTF-8 输出含 ¥ 的内容
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    main()
