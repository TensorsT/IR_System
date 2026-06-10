# 项目开发变更记录

本文件按时间记录 IR 综合实践的主要开发与数据处理工作。实验评测日志另见 `outputs/eval_run_log.md`。

---

## 2026-06-10

### 中英文跨语言检索

**动机**：语料以中文为主，用户输入 `NLP`、`ML`、`events extraction` 等英文时无法等价命中「自然语言处理」「机器学习」「事件抽取」。

**实现**（`ir_system.py`）：

- 新增 `QueryPlan`：原始查询 + 扩展短语 + 扩展词项
- `_ABBR_ALIASES`：149 个英文缩写 → 中文（NLP/ML/GNN/RAG/5G/SDN/FPGA/bioinfo…）
- `_EN_PHRASE_RULES`：142 条英文短语正则 → 中文
- 公开 API：`expand_query()`、`query_matches_text()`
- `search()` 对扩展短语做精确匹配、对扩展词项做倒排检索；模糊兜底同样遍历扩展短语
- `ir_gui.py`：侧栏过滤复用 `query_matches_text`；快捷查询加入 NLP/ML/events extraction/GNN 等英文按钮

**验证**：`NLP`→周国栋；`events extraction`→李培峰；`GNN`→周经亚；`evaluate.py` 21 条中文用例无回归（hit@1 85.71%）。

---

## 2026-06-09（晚）

### 论文专项筛查 `crawler/llm_papers_screen.py`

**动机**：赵雷等教师论文栏混入 ICDE/TKDE 缩写、统计句，非真实论文标题。

**实现**：

- 极短 DeepSeek prompt，只输出 `{title, venue, year, ccf_rank}`
- 无具体标题则清空；标记 `papers_screened: true`
- `--suspicious-only` 14 人清噪音；`--all` 324 人全量结构化

**结果**：339 人全部 `papers_screened`；210 人有 `papers_struct`；赵雷论文栏清空；向德辉抽出 10 条真实论文。

**日志**：`crawled_data/llm_papers_screen_log.txt`

---

## 2026-06-09

### 数据质量多轮修复

- `batch_fix_teachers.py`：无 API 批量修复占位论文、研究方向、生成 `papers_struct`
- `llm_extract.py` 全量抽取：331/339 成功（杨哲 1 人失败）；checkpoint + Ctrl+C 安全保存
- `sanitize_papers_record` / `_is_paper_line_noise`：过滤基金、课程、审稿、期刊缩写
- `repair_papers_record`：修复丁泓铭类 dict 字符串论文
- 研究方向误抽修复：邝泉声、陈虞苏等

### 检索与 GUI 优化

- 多次迭代查询、字段限定、结果清洗、关键词高亮去碎片
- `papers_struct` + GUI CCF A/B/C 彩色 badge
- 基础 vs 优化并排对比窗口

### 评测

- `evaluate.py`：21 条查询，baseline vs optimized
- 数据清洗后 hit@1：80.95% → 85.71%

---

## 2026-06-08 及更早

### 数据采集 V2 `crawler/e9_crawler_v2.py`

- 339 教师入库（5 学院），338 个 HTML（瞿剑锋 403）
- 模块化抽取 `div.post.mbox`、子页面跟进、多编码、 `full_text` 全量保留
- 输出：`teachers.json`、`corpus/`、`quality_report.json`

### 检索系统初版

- `ir_system.py`：TF-IDF 倒排索引 + 短语/词项/模糊检索
- `ir_gui.py`：ttkbootstrap 卡片式界面
- `evaluate.py` + `outputs/eval_*.csv`

---

## 关键文件索引

| 文件 | 说明 |
|------|------|
| `ir_system.py` | 检索核心 + 跨语言扩展 + `build_display` |
| `ir_gui.py` | GUI + 快捷查询 + 对比模式 |
| `evaluate.py` | 效果评测 |
| `crawler/e9_crawler_v2.py` | 爬虫 V2 |
| `crawler/llm_extract.py` | LLM 全量结构化 |
| `crawler/llm_papers_screen.py` | 论文专项筛查 |
| `crawler/batch_fix_teachers.py` | 规则批量修复 |
| `crawled_data/teachers.json` | 教师元数据 |
| `crawled_data/corpus/` | 检索语料 |
| `outputs/eval_run_log.md` | 评测运行日志（自动追加） |
| `README.md` | 使用说明 + 实验过程与问题处理 |
