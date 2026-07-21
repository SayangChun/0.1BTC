#!/usr/bin/env python3
"""
从 data/transactions.csv 读取冷钱包提现记录，更新 README.md 中的进度与图表。

用法（在项目根目录执行）:
  python scripts/update_readme.py
"""

from __future__ import annotations

import csv
import re
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "transactions.csv"
README_PATH = ROOT / "README.md"
GOAL_BTC = 0.1

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


def _short_date(date_s: str) -> str:
    try:
        return datetime.strptime(date_s, "%Y-%m-%d").strftime("%y-%m-%d")
    except ValueError:
        return date_s


def mermaid_chart(transactions: list[dict], cumulative: list[float]) -> str:
    """生成 GitHub README 可渲染的累计图。

    说明：
    - 仅 1 个数据点时纯折线几乎看不见，因此叠加 bar，并补一个 0 起点。
    - 早期持仓远小于 0.1 时，纵轴按持仓缩放，否则点会贴在坐标轴底部。
    """
    if not transactions:
        return (
            "```mermaid\n"
            "xychart-beta\n"
            '    title "Cold wallet BTC (no data yet)"\n'
            '    x-axis ["—"]\n'
            '    y-axis "BTC" 0 --> 0.1\n'
            "    bar [0]\n"
            "```\n"
            "\n"
            "_暂无数据。添加提现记录并运行 `python scripts/update_readme.py` 后会生成图表。_"
        )

    # x 轴标签：日期简写，过多时抽样，避免 README 过长
    labels: list[str] = []
    values: list[float] = []
    n = len(transactions)
    if n <= 24:
        indices = list(range(n))
    else:
        step = max(1, (n - 1) // 23)
        indices = list(range(0, n, step))
        if indices[-1] != n - 1:
            indices.append(n - 1)

    for i in indices:
        labels.append(_short_date(transactions[i]["date"]))
        values.append(round(cumulative[i], 8))

    # 补 0 起点，让折线在只有 1～2 笔时也有“爬升”形状
    first_date = transactions[0]["date"]
    try:
        first_dt = datetime.strptime(first_date, "%Y-%m-%d")
        # 起点标在首次提现前一天（仅用于作图，不是真实记录）
        origin_label = (first_dt - timedelta(days=1)).strftime("%y-%m-%d")
    except ValueError:
        origin_label = "start"
    plot_labels = [origin_label, *labels]
    plot_values = [0.0, *values]

    data_max = max(plot_values) if plot_values else 0.0
    # 早期持仓：放大纵轴，避免 0.005 在 0→0.1 坐标上几乎看不见
    if data_max <= 0:
        y_max = GOAL_BTC
        scale_note = ""
    elif data_max < GOAL_BTC * 0.3:
        y_max = round(max(data_max * 2.0, data_max + 0.001, 0.01), 6)
        scale_note = (
            f"\n\n_纵轴当前按持仓放大显示（约 0 → {y_max} BTC），"
            f"便于观察早期增长；最终目标仍为 **{GOAL_BTC} BTC**。_"
        )
    else:
        y_max = GOAL_BTC if data_max <= GOAL_BTC else round(data_max * 1.15, 4)
        scale_note = f"\n\n_纵轴范围 0 → {y_max} BTC（目标 {GOAL_BTC} BTC）。_"

    x_axis = ", ".join(f'"{lb}"' for lb in plot_labels)
    series = ", ".join(str(v) for v in plot_values)

    # 标题用英文，兼容部分 Mermaid 渲染环境；中文说明放在图下方
    return (
        "```mermaid\n"
        "xychart-beta\n"
        '    title "Cold wallet cumulative BTC"\n'
        f"    x-axis [{x_axis}]\n"
        f'    y-axis "BTC" 0 --> {y_max}\n'
        f"    bar [{series}]\n"
        f"    line [{series}]\n"
        "```"
        f"{scale_note}"
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
        mermaid_chart(transactions, cumulative),
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
