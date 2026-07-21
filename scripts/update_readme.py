#!/usr/bin/env python3
"""
从 data/transactions.csv 读取冷钱包提现记录，更新 README.md 中的进度与图表。

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
README_PATH = ROOT / "README.md"
CHART_SVG_PATH = ROOT / "assets" / "cumulative_btc.svg"
GOAL_BTC = 0.1
# 图表 0 起点：首次购买比特币的日期（作图用，不计入提现明细）
CHART_ORIGIN_DATE = "2026-03-27"
# 图表横轴最右端：计划达成 0.1 BTC 的目标日期（仅作轴端，不绘制数据点）
CHART_TARGET_DATE = "2029-06-01"

MARKER_START = "<!-- AUTO-GENERATED:START -->"
MARKER_END = "<!-- AUTO-GENERATED:END -->"


def load_transactions(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows

    with path.open(encoding="utf-8-sig", newline="") as f:
        # 跳过空行与 # 注释行
        lines = []
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            lines.append(line)

    if not lines:
        return rows

    reader = csv.DictReader(lines)
    for raw in reader:
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


def write_chart_svg(transactions: list[dict], cumulative: list[float], path: Path) -> None:
    """按真实时间比例绘制累计折线 SVG。

    - 横轴：CHART_ORIGIN_DATE → CHART_TARGET_DATE（线性时间）
    - 目标日仅作为轴右端，不绘制任何数据点/线终点
    - 纵轴固定 0 → GOAL_BTC
    - 仅绘制起点与实际提现累计点（阶梯/折线连接）
    """
    origin = _parse_date(CHART_ORIGIN_DATE)
    target = _parse_date(CHART_TARGET_DATE)
    if target <= origin:
        raise ValueError("CHART_TARGET_DATE must be after CHART_ORIGIN_DATE")

    y_max = GOAL_BTC
    if cumulative:
        data_max = max(cumulative)
        if data_max > y_max:
            y_max = data_max * 1.05

    # 绘图点：起点 0 + 每笔提现后的累计（不包含目标日）
    points: list[tuple[date, float]] = [(origin, 0.0)]
    for t, cum in zip(transactions, cumulative):
        d = _parse_date(t["date"])
        if d < origin:
            continue
        # 同一天多笔：保留最后累计
        if points and points[-1][0] == d:
            points[-1] = (d, cum)
        else:
            points.append((d, cum))

    # 若最后数据日晚于目标日，仍绘制该点，但轴域至少覆盖数据
    axis_end = target
    if points:
        axis_end = max(target, points[-1][0])

    total_days = (axis_end - origin).days
    if total_days <= 0:
        total_days = 1

    # 画布与边距
    width, height = 920, 420
    margin_left, margin_right = 64, 28
    margin_top, margin_bottom = 48, 56
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    def x_of(d: date) -> float:
        return margin_left + ((d - origin).days / total_days) * plot_w

    def y_of(v: float) -> float:
        v = max(0.0, min(v, y_max))
        return margin_top + plot_h * (1.0 - v / y_max)

    # 折线路径（按真实日期位置）
    if len(points) == 1:
        # 仅起点：画一个点
        path_d = ""
    else:
        path_parts: list[str] = []
        for i, (d, v) in enumerate(points):
            cmd = "M" if i == 0 else "L"
            path_parts.append(f"{cmd}{x_of(d):.2f},{y_of(v):.2f}")
        path_d = " ".join(path_parts)

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
    # 大约 4 个中间刻度
    for i in range(1, 5):
        d = origin + timedelta(days=round(total_days * i / 5))
        if origin < d < axis_end:
            x_tick_dates.append(d)
    if axis_end not in x_tick_dates:
        x_tick_dates.append(axis_end)
    # 去重并排序
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

    # 数据点
    point_elems: list[str] = []
    for d, v in points:
        point_elems.append(
            f'<circle cx="{x_of(d):.2f}" cy="{y_of(v):.2f}" r="3.5" '
            f'fill="#2563eb" stroke="#ffffff" stroke-width="1.5"/>'
        )

    title = "Cold wallet cumulative BTC"
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
  <!-- line -->
  {f'<path d="{path_d}" fill="none" stroke="#2563eb" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>' if path_d else ""}
  <!-- points -->
  {"".join(point_elems)}
</svg>
'''
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")


def chart_markdown(transactions: list[dict], cumulative: list[float]) -> str:
    """写入 SVG 并返回 README 中引用图表的 Markdown。"""
    write_chart_svg(transactions, cumulative, CHART_SVG_PATH)
    # 相对路径，便于 GitHub 渲染；加 query 避免缓存旧图（用数据摘要）
    total = cumulative[-1] if cumulative else 0.0
    cache_bust = f"{len(transactions)}-{format_btc(total)}-{CHART_TARGET_DATE}"
    rel = CHART_SVG_PATH.relative_to(ROOT).as_posix()
    note = (
        f"\n\n_起点为首次购买日 `{CHART_ORIGIN_DATE}`（累计 0，尚未提现到冷钱包）；"
        f"横轴按真实时间比例，最右端为目标日 `{CHART_TARGET_DATE}`（**不绘制**数据点）；"
        f"纵轴固定 0 → {GOAL_BTC} BTC。_"
    )
    if not transactions:
        note = (
            f"\n\n_暂无冷钱包提现数据。图表起点为首次购买日 `{CHART_ORIGIN_DATE}`，"
            f"横轴最右端为目标日 `{CHART_TARGET_DATE}`（仅作轴端）。_"
        )
    return (
        f'![Cold wallet cumulative BTC]({rel}?v={cache_bust})\n'
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


def build_auto_section(transactions: list[dict]) -> str:
    cumulative: list[float] = []
    total = 0.0
    total_fiat_by_currency: dict[str, float] = {}

    for t in transactions:
        total += t["btc"]
        cumulative.append(total)
        if t["fiat_amount"] is not None:
            cur = t["fiat_currency"] if t["fiat_currency"] != "—" else "UNKNOWN"
            total_fiat_by_currency[cur] = (
                total_fiat_by_currency.get(cur, 0.0) + t["fiat_amount"]
            )

    ratio = total / GOAL_BTC if GOAL_BTC else 0.0
    pct = ratio * 100
    remaining = max(0.0, GOAL_BTC - total)
    bar = progress_bar(ratio)

    fiat_lines = []
    for cur, amount in sorted(total_fiat_by_currency.items()):
        fiat_lines.append(f"- **累计投入 ({cur})**: {amount:,.2f}")
    if not fiat_lines:
        fiat_lines.append("- **累计投入**: —")

    avg_cost_lines = []
    for cur, amount in sorted(total_fiat_by_currency.items()):
        if total > 0:
            avg = amount / total
            # USD 均价按常见报价取整展示（如 $72040）
            if cur.upper() == "USD":
                avg_cost_lines.append(
                    f"- **平均成本 (USD/BTC)**: ${avg:,.0f}"
                )
            else:
                avg_cost_lines.append(
                    f"- **平均成本 ({cur}/BTC)**: {avg:,.2f}"
                )

    updated = datetime.now().strftime("%Y-%m-%d %H:%M")

    parts = [
        f"> 自动生成于 `{updated}` · 目标 **{GOAL_BTC} BTC** · 数据源 `data/transactions.csv`",
        "",
        "## 进度总览",
        "",
        f"**{format_btc(total)} / {GOAL_BTC} BTC**  ·  **{pct:.2f}%**",
        "",
        f"`{bar}`",
        "",
        f"- **冷钱包累计**: {format_btc(total)} BTC",
        f"- **距离目标还差**: {format_btc(remaining)} BTC",
        f"- **提现笔数**: {len(transactions)}",
        *fiat_lines,
        *avg_cost_lines,
        "",
        "## 冷钱包累计曲线",
        "",
        chart_markdown(transactions, cumulative),
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
    auto = build_auto_section(txs)
    update_readme(README_PATH, auto)
    total = sum(t["btc"] for t in txs)
    print(f"已更新 README.md：{len(txs)} 笔记录，合计 {format_btc(total)} BTC")


if __name__ == "__main__":
    main()
