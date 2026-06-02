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

## 使用方式

1. 命令行: 运行 `python ir_system.py`.
2. 图形界面: 运行 `python ir_gui.py`.

## 依赖

- 标准库: `json`, `math`, `os`, `re`, `tkinter`
- 第三方:
  - `ttkbootstrap`
  - `fuzzywuzzy` (可选, 启用模糊查询)

## 说明

- 语料位于 `crawled_data/corpus`.
- 教师元数据位于 `crawled_data/teachers.json`.
