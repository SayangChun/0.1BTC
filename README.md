# 0.1 BTC

在 GitHub 上记录我囤积 **0.1 BTC** 的过程。  
**不设在线网站** —— 打开本仓库的 README，即可查看进度与图表。

> **记录范围**：只统计**提现到冷钱包**的比特币。  
> 交易所账户、热钱包、链上未提现部分均**不记入**本仓库。

---

## 如何更新记录

1. 编辑 [`data/transactions.csv`](data/transactions.csv)，按行追加冷钱包提现：

   ```csv
   date,btc,fiat_amount,fiat_currency,note
   2026-06-08,0.005231,,,首次提现到冷钱包
   2026-07-20,0.002,1200,CNY,第二次提现
   ```

   | 字段 | 说明 |
   | --- | --- |
   | `date` | 提现到冷钱包的日期，`YYYY-MM-DD` |
   | `btc` | 本次到账冷钱包的比特币数量 |
   | `fiat_amount` | 对应成本/花费的法币金额（可空） |
   | `fiat_currency` | 法币币种，如 `CNY` / `USD`（可空） |
   | `note` | 备注（可空） |

2. 在项目根目录运行脚本，刷新下方进度与图表：

   ```bash
   python scripts/update_readme.py
   ```

3. 提交并推送到 GitHub：

   ```bash
   git add data/transactions.csv README.md
   git commit -m "记录：新增冷钱包提现"
   git push
   ```

图表使用 [Mermaid](https://mermaid.js.org/) 的 `xychart`，GitHub 会在 README 中直接渲染，无需部署任何网页。

---

<!-- AUTO-GENERATED:START -->
> 自动生成于 `2026-07-21 10:24` · 目标 **0.1 BTC** · 数据源 `data/transactions.csv`

## 进度总览

**0.005231 / 0.1 BTC**  ·  **5.23%**

`█░░░░░░░░░░░░░░░░░░░`

- **冷钱包累计**: 0.005231 BTC
- **距离目标还差**: 0.094769 BTC
- **提现笔数**: 1
- **累计投入**: —

## 冷钱包累计曲线

```mermaid
xychart-beta
    title "冷钱包累计 BTC"
    x-axis ["26-06-08"]
    y-axis "BTC" 0 --> 0.1
    line [0.005231]
```

## 提现明细

| 日期 | 提现 (BTC) | 累计 (BTC) | 法币金额 | 币种 | 备注 |
| --- | ---: | ---: | ---: | --- | --- |
| 2026-06-08 | 0.005231 | 0.005231 | — | — | 首次提现到冷钱包 |
<!-- AUTO-GENERATED:END -->
