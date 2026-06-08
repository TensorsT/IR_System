# IR System

一个面向苏州大学导师信息的轻量检索系统，提供 CLI 与 GUI 两种使用方式。

## 更新与功能

- 多次迭代查询: 先尝试精确短语匹配, 若无结果再放宽为分块/词项检索.
- 放宽查询条件: 例如 "机器翻译" 可自动降级为 "机器" 或 "翻译" 的组合检索.
- 可选模糊查询: 当存在 `fuzzywuzzy` 依赖时, 进行字符串模糊匹配以提升召回.

## 功能概览

- TF-IDF 词项打分与结果排序.
- 支持姓名/研究方向/论文等信息检索.
- GUI 过滤器: 姓名/研究方向/论文条件组合.
- GUI 增强: 快捷查询、基础/优化模式切换、Top-K 可调、按相关度/姓名/学院排序、状态栏耗时反馈、一键打开教师主页。

## 使用方式

1. 命令行: 运行 `python ir_system.py`.
2. 图形界面: 运行 `python ir_gui.py`.
3. 效果对比评测: 运行 `python evaluate.py`.

## 依赖

- 标准库: `json`, `math`, `os`, `re`, `tkinter`
- 第三方:
  - `ttkbootstrap`
  - `fuzzywuzzy` (可选, 启用模糊查询)

## 说明

- 语料位于 `crawled_data/corpus`.
- 教师元数据位于 `crawled_data/teachers.json`.
- 爬虫脚本位于 `crawler/e9_crawler.py`（可复现采集流程）。

## 评测输出

- `evaluate.py` 会同时评测 baseline（不放宽、不模糊）与 optimized（放宽+模糊）两套检索配置。
- 默认内置 20 条评测查询（姓名/研究方向/论文关键词混合）。
- 详细结果默认输出到 `outputs/eval_compare.csv`，包含 `hit@1`、`hit@k`、期望名次、响应时间等字段，可直接用于报告对比表。
- 汇总结果默认输出到 `outputs/eval_summary.csv`，可直接用于画柱状图/表格。
- 每次执行会把“本次做了什么 + 指标摘要”追加写入 `outputs/eval_run_log.md` 作为实验过程日志。

## 一键复现评测

```powershell
python evaluate.py
```

运行后会生成：

- `outputs/eval_compare.csv`（逐查询明细）
- `outputs/eval_summary.csv`（模式汇总）
- `outputs/eval_run_log.md`（运行日志，追加写入）
