## 采集结果

| 学院 | 入库人数 | 官网约 |
|------|----------|--------|
| 计算机科学与技术学院（软件学院） | 101 | 101 |
| 数学科学学院 | 66 | 66 |
| 物理科学与技术学院 | 61 | 61 |
| 电子信息学院 | 64 | 64 |
| 外国语学院 | 47 | 47 |
| **合计** | **339** | **339** |

- PPT中提到的 **周国栋** 已入库（`gdzhou`，含 NLP/信息抽取等研究方向）
- **名录 339 人**与官网 `count` 一致；**主页 HTML 338 个**（瞿剑锋 `jjf2` 仍 403，仅有名录无正文）
- 默认 `MAX_PER_COLLEGE=0` 全量；`hot`+`new` 双通道翻页

## 反爬策略（脚本内常量）

- 列表 API：优先 **hot** 排序（计算机学院覆盖更好），凑满配额即停（约 13 页，未扫完全站 32 页）
- 默认 **全量**（`MAX_PER_COLLEGE=0`，hot+new 双通道翻至最后一页）
- 请求间隔 **2.5–4.5s**；每 8 次 API 休息 **6s**；每 10 个主页休息 **8s**
- 仅 `suda.edu.cn`；电话/邮箱已脱敏

## 输出目录（对接 IR）

```
作业9 IR综合实践项目/crawled_data/
├── meta.json              # 学院元数据
├── teacher_index.json     # 轻量索引（doc_id、姓名、学院、URL）
├── teachers.json          # 完整结构化记录
├── docno.txt              # doc_id ↔ 语料文件名
├── corpus/*.txt           # 180 篇建索引用语料
├── raw_html/*.html        # 原始页面备份
├── crawl_state.json       # 教师名录快照
└── crawl_log.txt          # 运行日志
```

每条 `corpus/*.txt` 含：`姓名、职称、学院、研究领域、简介、论文` 等字段，可直接用于倒排索引 / BM25。

## 使用方式

```powershell
$env:HTTP_PROXY=''; $env:HTTPS_PROXY=''
cd "e:\信息检索\作业9 IR综合实践项目"
python e9_crawler.py                    # 默认每学院 35 人，断点续爬
python e9_crawler.py --max-per-college 50   # 加大规模（更慢、压力更大）
python e9_crawler.py --no-resume        # 全量重下主页
```

主脚本：`e:\信息检索\作业9 IR综合实践项目\e9_crawler.py`

---

下一步若要建 **基础 BM25 IR**（含「自然语言处理」「周国栋」等查询），可以直接以 `crawled_data/corpus/` + `docno.txt` 为语料入口。需要的话我可以接着写索引与检索模块。