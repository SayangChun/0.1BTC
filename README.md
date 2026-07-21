# 0.1 BTC

在 GitHub 上记录我囤积 **0.1 BTC** 的过程。  
**不设在线网站** —— 打开本仓库的 README，即可查看进度与图表。

---

## 如何更新记录

1. 编辑 [`data/transactions.csv`](data/transactions.csv)，按行追加买入：

   ```csv
   date,btc,fiat_amount,fiat_currency,note
   2026-01-15,0.001,650,CNY,首次买入
   2026-02-01,0.002,1200,CNY,定投
   ```

   | 字段 | 说明 |
   | --- | --- |
   | `date` | 买入日期，`YYYY-MM-DD` |
   | `btc` | 本次买入的比特币数量 |
   | `fiat_amount` | 本次花费的法币金额（可空） |
   | `fiat_currency` | 法币币种，如 `CNY` / `USD` |
   | `note` | 备注（可空） |

2. 在项目根目录运行脚本，刷新下方进度与图表：

   ```bash
   python scripts/update_readme.py
   ```

3. 提交并推送到 GitHub：

   ```bash
   git add data/transactions.csv README.md
   git commit -m "记录：新增囤币"
   git push
   ```

图表使用 [Mermaid](https://mermaid.js.org/) 的 `xychart`，GitHub 会在 README 中直接渲染，无需部署任何网页。

---

<!-- AUTO-GENERATED:START -->
> 自动生成于 `2026-07-21 10:22` · 目标 **0.1 BTC** · 数据源 `data/transactions.csv`

## 进度总览

**0 / 0.1 BTC**  ·  **0.00%**

`░░░░░░░░░░░░░░░░░░░░`

- **当前持有**: 0 BTC
- **距离目标还差**: 0.1 BTC
- **买入笔数**: 0
- **累计投入**: —

## 累计持有曲线

```mermaid
xychart-beta
    title "累计持有 BTC（暂无数据）"
    x-axis [—]
    y-axis "BTC" 0 --> 0.1
    line [0]
```

## 买入明细

_暂无记录。请在 `data/transactions.csv` 中添加买入记录。_
<!-- AUTO-GENERATED:END -->
