## 2026-06-08 11:07:32

### 本次执行内容
- 执行了 baseline（不放宽、不模糊）评测。
- 执行了 optimized（放宽+模糊）评测。
- 评测查询数: 21，top_k: 5。
- 数据规模: teachers=339, docs=339。
- 产出明细: `outputs/eval_compare.csv`。
- 产出汇总: `outputs/eval_summary.csv`。

### 指标摘要
- baseline: hit@1=17/21 (80.95%), hit@k=19/21 (90.48%), avg_latency=0.61ms
- optimized: hit@1=19/21 (90.48%), hit@k=21/21 (100.00%), avg_latency=5.84ms

---
