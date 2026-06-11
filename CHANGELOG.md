# 项目开发变更记录

本文件按时间记录 IR 综合实践的主要开发与数据处理工作。实验评测日志另见 `outputs/eval_run_log.md`。

---

## 2026-06-11

### 排序逻辑修正：导航性尾缀规范化 + 字段分层权重 + TF-IDF 评分

**问题背景**：

1. **「自然语言处理」vs「自然语言处理方向」结果差异大**：前者 `phrase_search` 精确命中后直接返回高质量 NLP 教师；后者因文档中不存在完整字符串 `自然语言处理方向` 而退化为 `token_search`，尾缀词「方向」的 bigram 匹配大面积污染结果集。
2. **「信息提取」搜到电子信息学院老师**：`_token_search` 评分只用 TF 未乘 IDF，「信息」是院系名高频词，TF 高 → 无关教师得分高。同时院系名字段的文本与研究方向的文本混在同一个 haystack/索引中，无法区分来源。

**修改文件**：`ir_system.py`、`ir_gui.py`、`evaluate.py`

---

#### 优化一：导航性尾缀规范化

**新增**（`ir_system.py`）：

- `_NAV_SUFFIX_WORDS`：12 个导航性尾缀词集合（`方向、研究、领域、相关、方面、技术、应用、研究方向、研究领域、相关领域、相关技术、技术方向、应用方向`）
- `_strip_nav_suffix(query)`：若查询以尾缀词结尾且截断后长度≥2，返回核心词；按尾缀长度降序匹配（优先最长匹配）

**修改**（`_build_query_plan`）：

- 在生成 `phrases` 前调用 `_strip_nav_suffix`，若存在核心词则插入到 `phrases` 列表最前（先于 `original`），并去重。
- 效果：「自然语言处理方向」→ `phrases = ['自然语言处理', '自然语言处理方向']`，`phrase_search` 优先生效，不再退化为 token_search。

**验证**：搜「自然语言处理方向」结果与「自然语言处理」完全一致（均为 NLP 教师）；「信息提取」「低资源跨语言」等无导航尾缀的查询不受影响。

---

#### 优化二：字段分层权重与 TF-IDF 评分

**修改 A — `_phrase_search` 排除院系名/职称字段**：

- 构建 teacher haystack 时，从 `name + department + career + research_direction + personal_intro + papers_text` 改为 `name + research_direction + personal_intro + papers_text`
- `department`（院系名）和 `career`（职称）不再参与 phrase 匹配，仅用于展示

**修改 B — `_token_search` 引入 IDF 加权**：

- 函数签名新增 `idf: Dict[str, float] | None` 参数
- 评分从 `weight = 1 + log(tf)` 改为 `weight = (1 + log(tf)) × idf_val`
- 「信息」「学院」「技术」等高频词 IDF 接近 0，不再拉高无关文档得分
- `idf=None` 时退化为纯 TF（向后兼容）

**修改 C — `build_index` 返回值变更**：

- 从 `(inverted, doc_norms)` 改为 `(inverted, doc_norms, idf)`
- 新增返回 `idf` 字典，供 `_token_search` 进行 TF-IDF 加权
- `doc_norms` 计算本身已使用 IDF（原代码即如此），无需修改

**修改 D — `search()` 插入字段优先搜索层**：

- `phrase_search` 失败后，先依次调用 `_field_search(plan, "research")` 和 `_field_search(plan, "papers")`，在「研究方向」和「论文」字段内 token 搜索
- 只有字段搜索也失败，才降级为全文 `_token_search`
- 同时将 `idf` 传入 `_token_search`
- 执行顺序：精确短语匹配 → 字段优先层（research → papers）→ 全文 token 搜索 → 模糊兜底

**同步更新**：

- `ir_gui.py`：`build_index` 解包改为三元组 `(inverted, doc_norms, idf)`；三处 `search()` 调用均传入 `idf=idf`
- `evaluate.py`：`load_resources()` 新增返回 `idf`；`evaluate_mode` 接收并传入 `idf`；主流程两处调用同步更新

**验证**：
- 「信息抽取」→ 全部 NLP 教师（周国栋、洪宇、李培峰等）✅
- 「信息提取」→ 结果中不再混入纯电子信息学院的无关教师 ✅
- 「自然语言处理方向」→ 与「自然语言处理」结果一致 ✅

### 2026-06-10

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
| `ir_system.py` | 检索核心 + 跨语言扩展 + 导航性尾缀规范化 + 字段分层权重 + TF-IDF 评分 |
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
