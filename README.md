# IR System

一个面向苏州大学导师信息的轻量检索系统，提供 CLI 与 GUI 两种使用方式。

## 更新与功能

- 多次迭代查询: 先尝试精确短语匹配, 若无结果再放宽为分块/词项检索.
- 放宽查询条件: 例如 "机器翻译" 可自动降级为 "机器" 或 "翻译" 的组合检索.
- 可选模糊查询: 当存在 `fuzzywuzzy` 依赖时, 进行字符串模糊匹配以提升召回.
- 字段限定检索: `姓名: / 研究方向: / 论文:` 前缀可将检索范围限定到对应字段, 提升精确度.
- 结果清洗去冗余: 自动剥离语料元数据头(doc_id/url 等)、裁剪页面模板与页脚(基本信息/版权所有 等)、折叠多余空白, 并对研究方向/简介/论文做长度截断.
- 片段智能去重: 当片段内容与已展示字段重复时自动隐藏.
- 命中关键词高亮: 仅保留最长非重叠匹配词(如 "周国栋" 不再附带 "周国"/"栋" 碎片).
- 更稳健脱敏: 邮箱需带合法域名(避免误伤 "@Google Scholar"), 电话仅匹配手机号/带分隔符座机(避免误伤课题编号与年份区间).

## 功能概览

- TF-IDF 词项打分与结果排序.
- 支持姓名/研究方向/论文等信息检索.
- GUI 过滤器: 姓名/研究方向/论文条件组合.
- GUI 增强: 快捷查询、基础/优化模式切换、Top-K 可调、按相关度/姓名/学院排序、状态栏耗时反馈、一键打开教师主页。
- GUI 美化: 卡片式结果、序号徽章、相关度徽章、字段标签对齐、命中关键词彩色标签、分隔线与滚轮滚动。
- 基础 vs 优化并排对比: 点击「基础 vs 优化 对比」按钮, 弹出双栏窗口同屏比较两套配置的结果, 标注各自命中数/耗时, 并用绿色「优化新增」标签高亮优化模式额外召回的导师。

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
- 爬虫脚本: `crawler/e9_crawler.py`（V1）, `crawler/e9_crawler_v2.py`（V2 高质量版）。

## 高质量爬虫 V2（crawler/e9_crawler_v2.py）

针对 V1 数据中“研究方向只抓到引导词/标签、片段混入导航菜单、保存乱码”等问题重写：

- 结构化抽取: 按页面模块 `div.post.mbox`（标题 `.tt .tit` + 正文 `div.con`）精准取栏目内容, 不再对整页扁平文本硬切。
- 多页站点跟进: 自建主页(如 `jy_zhou/index.html`)若研究/论文拆到子页面(`research.html`/`papers.html`), 自动顺着「科研/论文/简介」链接抓取并补齐。
- 英文主页适配: 支持 `Biography / Research / Publications` 等英文栏目标题(如赵朋朋英文主页)。
- 标题别名适配: 兼容 `个人简历 / 个人概况` 等非标准栏目名(如曹敏)。
- 强力清洗: 去导航行/空标签行/访问计数/重复段落, 折叠空白, 归一化不间断空格; 扁平分节会在「社会兼职/招生信息」等非目标标题处截断, 避免串栏。
- 字段校验: 研究方向/简介/论文若仅剩残缺标签(如 `研究方向：`)判定为缺失并置空。
- 全量摘录: 每位老师把主页+子页面的干净全文存入记录字段 `full_text`(已脱敏), 供大模型抽取阶段使用, 做到不丢信息。
- 健壮编码: 响应头 charset → meta charset → utf-8 → gb18030, 修复保存乱码。
- 质量报告: 输出 `crawled_data/quality_report.json`, 统计各字段完整率并列出缺失项。

## 大模型结构化抽取（crawler/llm_extract.py，可选增强）

各教师主页栏目命名差异极大（研究领域/Research/会议论文/期刊论文…），关键词匹配易漏。
该脚本把爬虫存的 `full_text` 整体交给大模型，统一抽取 研究方向/简介/代表论文/关键词，
不依赖固定栏目名；属“规则保底 + LLM 增强”：

- 发送前再次脱敏；输出严格 JSON 带容错解析；限速/重试/退避；缓存(已抽取跳过, `--force` 重抽)。
- 没有 API key 或调用失败时自动回退保留规则版结果, 不清空已有数据。
- 抽取后回写 `teachers.json` 并重建语料/索引, IR 系统可直接使用。

依赖与环境变量（DeepSeek，OpenAI SDK）:

- `pip install openai`
- `DEEPSEEK_API_KEY`(必填，兼容旧名 `LLM_API_KEY`)、`LLM_BASE_URL`(默认 `https://api.deepseek.com`)、`LLM_MODEL`(默认 `deepseek-v4-pro`)。
- 默认开启 DeepSeek V4 Pro 思考模式(`reasoning_effort=high` + `thinking.enabled`)，可用 `--no-thinking` 关闭以提速省钱。

```powershell
$env:DEEPSEEK_API_KEY="sk-xxx"
python crawler/llm_extract.py --limit 5      # 先小批量试抽
python crawler/llm_extract.py                # 全量抽取并回写
python crawler/llm_extract.py --dry-run      # 只打印不回写
python crawler/llm_extract.py --no-thinking  # 关闭思考模式
```

### 研究方向从简介回填（规则保底，无需 API）

部分老师把研究方向写在“个人简介”里（如“研究兴趣集中在…/主要从事…研究”）。
爬虫与 `llm_extract.py` 会在研究方向缺失时，从简介中按高精度规则抽取并过滤教学/联系等噪音：

```powershell
python crawler/llm_extract.py --rules-only   # 无需 key，从简介回填研究方向并重建语料
```
- 温和反爬: 随机间隔(4–7s)、分批长休息、429/503 指数退避并尊重 `Retry-After`、403 退避重试、仅抓 `suda.edu.cn` 静态主页。

运行方式（建议先小批量试跑）:

```powershell
# 清掉本地代理干扰
$env:HTTP_PROXY=''; $env:HTTPS_PROXY=''
cd "e:\信息检索\github_IR\IR_System"

# 1) 先试跑前 10 位, 确认数据质量与反爬正常
python crawler/e9_crawler_v2.py --limit 10

# 2) 确认无误后全量重爬（会重新生成 teachers.json / corpus 等）
python crawler/e9_crawler_v2.py --no-resume
```

说明: V2 只复用带 `parser_version=2` 的记录, 因此首次运行会对旧数据强制重爬以提升质量; 中断后可去掉 `--no-resume` 续爬。

## 评测输出

- `evaluate.py` 会同时评测 baseline（不放宽、不模糊）与 optimized（放宽+模糊）两套检索配置。
- 默认内置 21 条评测查询（姓名/研究方向/字段限定混合）。
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
