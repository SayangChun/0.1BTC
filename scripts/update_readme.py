#!/usr/bin/env python3
"""
从 data/transactions.csv 读取冷钱包提现记录，更新 README.md 中的进度与图表。

用法（在项目根目录执行）:
  python scripts/update_readme.py
"""

from __future__ import annotations

import csv
import re
from datetime import datetime
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


def mermaid_chart(transactions: list[dict], cumulative: list[float]) -> str:
    if not transactions:
        return (
            "```mermaid\n"
            "xychart-beta\n"
            '    title "冷钱包累计 BTC（暂无数据）"\n'
            "    x-axis [—]\n"
            '    y-axis "BTC" 0 --> 0.1\n'
            "    line [0]\n"
            "```"
        )

    # x 轴标签：日期简写，过多时抽样，避免 README 过长
    labels = []
    values = []
    n = len(transactions)
    if n <= 24:
        indices = list(range(n))
    else:
        step = max(1, (n - 1) // 23)
        indices = list(range(0, n, step))
        if indices[-1] != n - 1:
            indices.append(n - 1)

    for i in indices:
        d = transactions[i]["date"]
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
            label = dt.strftime("%y-%m-%d")
        except ValueError:
            label = d
        labels.append(label)
        values.append(round(cumulative[i], 8))

    # Mermaid 标签含特殊字符时用引号
    x_axis = ", ".join(f'"{lb}"' for lb in labels)
    y_max = max(GOAL_BTC, max(values) * 1.15 if values else GOAL_BTC)
    y_max = round(y_max, 4)
    line_vals = ", ".join(str(v) for v in values)

    return (
        "```mermaid\n"
        "xychart-beta\n"
        '    title "冷钱包累计 BTC"\n'
        f"    x-axis [{x_axis}]\n"
        f'    y-axis "BTC" 0 --> {y_max}\n'
        f"    line [{line_vals}]\n"
        "```"
    )


def build_table(transactions: list[dict], cumulative: list[float]) -> str:
    if not transactions:
        return "_暂无记录。请在 `data/transactions.csv` 中添加提现到冷钱包的记录。_"

    lines = [
        "| 日期 | 提现 (BTC) | 累计 (BTC) | 法币金额 | 币种 | 备注 |",
        "| --- | ---: | ---: | ---: | --- | --- |",
    ]
    for t, cum in zip(transactions, cumulative):
        fiat = (
            f"{t['fiat_amount']:,.2f}"
            if t["fiat_amount"] is not None
            else "—"
        )
        lines.append(
            f"| {t['date']} | {format_btc(t['btc'])} | {format_btc(cum)} | "
            f"{fiat} | {t['fiat_currency']} | {t['note']} |"
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
