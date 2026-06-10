import ast
import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple

try:
    from fuzzywuzzy import fuzz

    _FUZZY_AVAILABLE = True
except Exception:
    _FUZZY_AVAILABLE = False


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "crawled_data")
CORPUS_DIR = os.path.join(DATA_DIR, "corpus")
TEACHERS_JSON = os.path.join(DATA_DIR, "teachers.json")


@dataclass
class DocRecord:
    doc_id: str
    path: str
    text: str


@dataclass
class TeacherRecord:
    name: str
    department: str
    career: str
    url: str
    research_direction: str
    personal_intro: str
    papers_text: str
    profile_keywords: List[str] = field(default_factory=list)
    papers_struct: List[dict] = field(default_factory=list)


@dataclass
class SearchResult:
    score: float
    doc: DocRecord
    teacher: TeacherRecord
    snippet: str


@dataclass
class PaperItem:
    title: str
    venue: str = ""
    year: str = ""
    ccf_rank: str = ""


@dataclass
class DisplayResult:
    rank: int
    name: str
    department: str
    career: str
    research: str
    intro: str
    papers: str
    snippet: str
    url: str
    score: float
    keywords: List[str]
    paper_items: List[PaperItem] = field(default_factory=list)
    research_tags: List[str] = field(default_factory=list)
    profile_keywords: List[str] = field(default_factory=list)


@dataclass
class QueryPlan:
    """检索计划：原始查询 + 跨语言扩展后的短语/词项。"""

    original: str
    phrases: List[str]
    tokens: List[str]


# 英文缩写 → 中文等价词（独立单词命中时扩展）
_ABBR_ALIASES: Dict[str, List[str]] = {
    # ── 人工智能 / NLP ──
    "nlp": ["自然语言处理", "中文信息处理"],
    "nlu": ["自然语言理解"],
    "nlg": ["自然语言生成"],
    "ml": ["机器学习"],
    "ai": ["人工智能"],
    "dl": ["深度学习"],
    "drl": ["深度强化学习", "强化学习"],
    "cv": ["计算机视觉"],
    "ir": ["信息检索", "信息抽取"],
    "ie": ["信息抽取"],
    "mt": ["机器翻译", "神经机器翻译"],
    "nmt": ["神经机器翻译", "机器翻译"],
    "ner": ["命名实体识别", "实体识别"],
    "re": ["关系抽取", "事件关系抽取"],
    "ee": ["事件抽取"],
    "srl": ["语义角色标注"],
    "amr": ["抽象语义表示", "语义分析"],
    "pos": ["词法分析", "词法句法分析"],
    "qa": ["问答系统", "大模型问答", "智能问答"],
    "chatbot": ["对话系统", "聊天机器人"],
    "kbqa": ["知识图谱问答", "知识问答"],
    "kg": ["知识图谱"],
    "llm": ["大语言模型", "大模型"],
    "llms": ["大语言模型", "大模型"],
    "lm": ["语言模型", "大语言模型"],
    "plm": ["预训练语言模型", "大语言模型"],
    "rag": ["检索增强生成"],
    "cot": ["思维链", "链式推理"],
    "prompt": ["提示学习", "提示工程"],
    "icl": ["上下文学习", "少样本学习"],
    "asr": ["语音识别", "自动语音识别"],
    "tts": ["语音合成", "语音转换"],
    "ocr": ["文字识别", "光学字符识别"],
    "mtl": ["多任务学习"],
    "multimodal": ["多模态", "多模态信息处理"],
    # ── 机器学习 / 数据 ──
    "dm": ["数据挖掘"],
    "bi": ["商业智能", "数据分析", "生物信息学"],
    "rs": ["推荐系统"],
    "cf": ["协同过滤", "推荐系统"],
    "gnn": ["图神经网络"],
    "gcn": ["图卷积网络", "图神经网络"],
    "gat": ["图注意力网络", "图神经网络"],
    "cnn": ["卷积神经网络", "深度学习"],
    "rnn": ["循环神经网络", "深度学习"],
    "lstm": ["长短期记忆网络", "循环神经网络"],
    "gru": ["门控循环单元", "循环神经网络"],
    "transformer": ["Transformer", "注意力机制"],
    "vit": ["视觉Transformer", "计算机视觉"],
    "bert": ["预训练语言模型", "自然语言处理"],
    "gpt": ["大语言模型", "生成式模型"],
    "vae": ["变分自编码器", "生成模型"],
    "gan": ["生成对抗网络", "深度学习"],
    "svm": ["支持向量机", "机器学习"],
    "knn": ["近邻算法", "机器学习"],
    "pca": ["主成分分析", "降维"],
    "lda": ["主题模型", "线性判别分析"],
    "rl": ["强化学习"],
    "dqn": ["深度强化学习", "强化学习"],
    "ppo": ["强化学习", "深度强化学习"],
    "nn": ["神经网络"],
    "mlp": ["多层感知机", "神经网络"],
    "sgd": ["随机梯度下降", "优化算法"],
    "adam": ["自适应优化", "优化算法"],
    "loss": ["损失函数", "目标函数"],
    "cl": ["对比学习", "表示学习"],
    "ssl": ["自监督学习", "表示学习"],
    "tl": ["迁移学习", "领域自适应"],
    "da": ["领域自适应", "迁移学习"],
    "fl": ["联邦学习", "隐私计算"],
    "xai": ["可解释人工智能", "可解释性"],
    "automl": ["自动机器学习", "机器学习"],
    "ts": ["时间序列", "时序分析"],
    "st": ["时空数据", "时空数据分析"],
    # ── 系统 / 软件 / 网络 ──
    "db": ["数据库", "图数据库"],
    "gdb": ["图数据库"],
    "sql": ["数据库", "结构化查询"],
    "nosql": ["非关系数据库", "分布式数据库"],
    "os": ["操作系统", "系统软件"],
    "se": ["软件工程", "可信软件"],
    "iot": ["物联网"],
    "iiot": ["工业互联网", "物联网"],
    "edge": ["边缘计算", "移动边缘计算"],
    "mec": ["移动边缘计算", "边缘计算"],
    "cloud": ["云计算", "云原生"],
    "sdn": ["软件定义网络"],
    "nfv": ["网络功能虚拟化", "软件定义网络"],
    "5g": ["第五代移动通信", "无线通信"],
    "6g": ["第六代移动通信", "无线通信"],
    "wifi": ["无线局域网", "无线网络"],
    "wlan": ["无线局域网", "无线网络"],
    "manet": ["移动自组网", "无线网络"],
    "vanet": ["车联网", "物联网"],
    "ns": ["网络安全", "网络与信息安全"],
    "sec": ["信息安全", "网络安全"],
    "crypto": ["密码学", "信息安全"],
    "pqc": ["后量子密码", "密码学"],
    "blockchain": ["区块链", "分布式系统"],
    "hci": ["人机交互", "智能人机交互"],
    "ar": ["增强现实", "虚拟现实"],
    "vr": ["虚拟现实", "增强现实"],
    "xr": ["扩展现实", "虚拟现实"],
    "hpc": ["高性能计算", "并行计算"],
    "gpu": ["图形处理器", "并行计算"],
    "cuda": ["并行计算", "深度学习"],
    "fpga": ["现场可编程门阵列", "硬件加速"],
    "asic": ["专用集成电路", "芯片设计"],
    "soc": ["片上系统", "集成电路设计"],
    "dsp": ["数字信号处理", "信号处理"],
    "eda": ["电子设计自动化", "集成电路设计"],
    # ── 通信 / 电子 / 信号 ──
    "sp": ["信号处理", "智能信号处理"],
    "isp": ["图像信号处理", "图像处理"],
    "rf": ["射频", "微波技术"],
    "microwave": ["微波技术", "电磁场"],
    "antenna": ["天线设计", "天线"],
    "mimo": ["多输入多输出", "无线通信"],
    "ofdm": ["正交频分复用", "无线通信"],
    "qam": ["正交幅度调制", "通信信号处理"],
    "psk": ["相移键控", "调制解调"],
    "ber": ["误码率", "通信系统"],
    "snr": ["信噪比", "信号处理"],
    "csi": ["信道状态信息", "信道估计"],
    "beamforming": ["波束成形", "天线"],
    "massive": ["大规模天线", "MIMO"],
    "ris": ["智能超表面", "可重构超表面"],
    "uwb": ["超宽带", "无线定位"],
    "lidar": ["激光雷达", "点云"],
    "slam": ["同步定位与建图", "机器人"],
    "ros": ["机器人操作系统", "智能机器人"],
    # ── 光学 / 材料 / 物理（语料常见）──
    "led": ["发光二极管", "光电器件"],
    "oled": ["有机发光二极管", "光电子器件"],
    "pv": ["光伏", "太阳能电池"],
    "pec": ["光电化学", "光催化"],
    "dft": ["密度泛函理论", "计算物理"],
    "md": ["分子动力学", "计算模拟"],
    "sem": ["扫描电镜", "材料表征"],
    "tem": ["透射电镜", "材料表征"],
    "xrd": ["X射线衍射", "材料表征"],
    "nmr": ["核磁共振", "谱学"],
    # ── 生物 / 医学信息 ──
    "bioinfo": ["生物信息学", "生物信息"],
    "omics": ["组学", "生物信息学"],
    "ppi": ["蛋白质相互作用", "生物信息"],
    "drug": ["药物发现", "分子生成"],
    # ── 数学 / 优化 ──
    "opt": ["最优化", "优化算法"],
    "or": ["运筹学", "最优化"],
    "pde": ["偏微分方程", "数值方法"],
    "ode": ["常微分方程", "数值方法"],
    "fde": ["分数阶微分方程", "偏微分方程"],
    "fem": ["有限元", "有限元方法"],
    "fdm": ["有限差分", "有限差分法"],
}

# 英文短语 → 中文（较长模式优先）
_EN_PHRASE_RULES: List[Tuple[re.Pattern, List[str]]] = [
    # NLP / 文本
    (re.compile(r"natural\s+language\s+processing", re.I), ["自然语言处理", "中文信息处理"]),
    (re.compile(r"natural\s+language\s+understanding", re.I), ["自然语言理解"]),
    (re.compile(r"natural\s+language\s+generation", re.I), ["自然语言生成"]),
    (re.compile(r"chinese\s+information\s+processing", re.I), ["中文信息处理", "自然语言处理"]),
    (re.compile(r"cross[\s-]?lingual", re.I), ["跨语言", "跨语言信息处理"]),
    (re.compile(r"information\s+extraction", re.I), ["信息抽取"]),
    (re.compile(r"events?\s+extraction", re.I), ["事件抽取"]),
    (re.compile(r"event\s+extraction", re.I), ["事件抽取"]),
    (re.compile(r"relation\s+extraction", re.I), ["关系抽取", "事件关系抽取"]),
    (re.compile(r"entity\s+recognition", re.I), ["命名实体识别", "实体识别"]),
    (re.compile(r"named\s+entity", re.I), ["命名实体识别", "实体识别"]),
    (re.compile(r"semantic\s+role\s+labeling", re.I), ["语义角色标注"]),
    (re.compile(r"semantic\s+parsing", re.I), ["语义分析", "语义角色标注"]),
    (re.compile(r"discourse\s+analysis", re.I), ["篇章分析", "对话分析"]),
    (re.compile(r"coreference\s+resolution", re.I), ["指代消解", "实体指代消解"]),
    (re.compile(r"text\s+classification", re.I), ["文本分类", "自然语言处理"]),
    (re.compile(r"text\s+summarization", re.I), ["文本摘要", "自动摘要"]),
    (re.compile(r"text\s+mining", re.I), ["文本挖掘", "数据挖掘"]),
    (re.compile(r"sentiment\s+analysis", re.I), ["情感分析"]),
    (re.compile(r"opinion\s+mining", re.I), ["观点挖掘", "情感分析"]),
    (re.compile(r"question\s+answering", re.I), ["问答系统", "智能问答"]),
    (re.compile(r"dialogue\s+system", re.I), ["对话系统", "多轮对话"]),
    (re.compile(r"machine\s+translation", re.I), ["机器翻译", "神经机器翻译"]),
    (re.compile(r"neural\s+machine\s+translation", re.I), ["神经机器翻译", "机器翻译"]),
    (re.compile(r"speech\s+recognition", re.I), ["语音识别", "自动语音识别"]),
    (re.compile(r"speech\s+enhancement", re.I), ["语音增强", "单通道语音增强"]),
    (re.compile(r"speech\s+separation", re.I), ["语音分离"]),
    (re.compile(r"speaker\s+recognition", re.I), ["说话人识别"]),
    (re.compile(r"voice\s+conversion", re.I), ["语音转换"]),
    (re.compile(r"large\s+language\s+model", re.I), ["大语言模型", "大模型"]),
    (re.compile(r"pre[\s-]?trained\s+language\s+model", re.I), ["预训练语言模型", "大语言模型"]),
    (re.compile(r"retrieval[\s-]?augmented", re.I), ["检索增强生成", "RAG"]),
    (re.compile(r"prompt\s+learning", re.I), ["提示学习", "提示工程"]),
    (re.compile(r"chain\s+of\s+thought", re.I), ["思维链", "链式推理"]),
    (re.compile(r"few[\s-]?shot\s+learning", re.I), ["少样本学习", "小样本学习"]),
    (re.compile(r"zero[\s-]?shot", re.I), ["零样本学习", "零样本"]),
    (re.compile(r"multi[\s-]?modal", re.I), ["多模态", "多模态信息处理"]),
    (re.compile(r"knowledge\s+graph", re.I), ["知识图谱"]),
    (re.compile(r"graph\s+database", re.I), ["图数据库"]),
    (re.compile(r"rumor\s+detection", re.I), ["谣言检测"]),
    (re.compile(r"fake\s+news", re.I), ["谣言检测", "虚假信息检测"]),
    # ML / DL
    (re.compile(r"machine\s+learning", re.I), ["机器学习"]),
    (re.compile(r"deep\s+learning", re.I), ["深度学习"]),
    (re.compile(r"deep\s+reinforcement\s+learning", re.I), ["深度强化学习", "强化学习"]),
    (re.compile(r"reinforcement\s+learning", re.I), ["强化学习"]),
    (re.compile(r"supervised\s+learning", re.I), ["监督学习", "机器学习"]),
    (re.compile(r"unsupervised\s+learning", re.I), ["无监督学习", "机器学习"]),
    (re.compile(r"semi[\s-]?supervised", re.I), ["半监督学习", "机器学习"]),
    (re.compile(r"self[\s-]?supervised", re.I), ["自监督学习", "表示学习"]),
    (re.compile(r"transfer\s+learning", re.I), ["迁移学习"]),
    (re.compile(r"domain\s+adaptation", re.I), ["领域自适应", "迁移学习"]),
    (re.compile(r"contrastive\s+learning", re.I), ["对比学习", "表示学习"]),
    (re.compile(r"representation\s+learning", re.I), ["表示学习", "特征学习"]),
    (re.compile(r"federated\s+learning", re.I), ["联邦学习", "隐私计算"]),
    (re.compile(r"graph\s+neural\s+network", re.I), ["图神经网络"]),
    (re.compile(r"convolutional\s+neural\s+network", re.I), ["卷积神经网络", "深度学习"]),
    (re.compile(r"recurrent\s+neural\s+network", re.I), ["循环神经网络", "深度学习"]),
    (re.compile(r"neural\s+network", re.I), ["神经网络"]),
    (re.compile(r"attention\s+mechanism", re.I), ["注意力机制", "Transformer"]),
    (re.compile(r"generative\s+adversarial", re.I), ["生成对抗网络", "GAN"]),
    (re.compile(r"variational\s+autoencoder", re.I), ["变分自编码器", "生成模型"]),
    (re.compile(r"auto[\s-]?encoder", re.I), ["自编码器", "表示学习"]),
    (re.compile(r"support\s+vector\s+machine", re.I), ["支持向量机", "机器学习"]),
    (re.compile(r"random\s+forest", re.I), ["随机森林", "机器学习"]),
    (re.compile(r"decision\s+tree", re.I), ["决策树", "机器学习"]),
    (re.compile(r"ensemble\s+learning", re.I), ["集成学习", "机器学习"]),
    (re.compile(r"feature\s+engineering", re.I), ["特征工程", "数据挖掘"]),
    (re.compile(r"hyperparameter", re.I), ["超参数", "模型调优"]),
    (re.compile(r"model\s+compression", re.I), ["模型压缩", "模型加速"]),
    (re.compile(r"model\s+inference", re.I), ["模型推理", "大模型推理"]),
    (re.compile(r"explainable\s+ai", re.I), ["可解释人工智能", "可解释性"]),
    # CV / 图像
    (re.compile(r"computer\s+vision", re.I), ["计算机视觉"]),
    (re.compile(r"image\s+processing", re.I), ["图像处理", "医学影像处理"]),
    (re.compile(r"image\s+segmentation", re.I), ["图像分割", "医学影像处理"]),
    (re.compile(r"object\s+detection", re.I), ["目标检测", "计算机视觉"]),
    (re.compile(r"face\s+recognition", re.I), ["人脸识别", "人脸活体检测"]),
    (re.compile(r"optical\s+character\s+recognition", re.I), ["光学字符识别", "文字识别"]),
    (re.compile(r"medical\s+image", re.I), ["医学影像", "医学影像处理"]),
    (re.compile(r"remote\s+sensing", re.I), ["遥感", "高光谱遥感"]),
    (re.compile(r"hyperspectral", re.I), ["高光谱", "高光谱遥感"]),
    (re.compile(r"point\s+cloud", re.I), ["点云", "三维点云"]),
    (re.compile(r"3d\s+vision", re.I), ["三维视觉", "3D机器视觉"]),
    # 数据 / 推荐 / 挖掘
    (re.compile(r"data\s+mining", re.I), ["数据挖掘"]),
    (re.compile(r"big\s+data", re.I), ["大数据", "大数据分析"]),
    (re.compile(r"data\s+analysis", re.I), ["数据分析", "数据处理"]),
    (re.compile(r"recommendation\s+system", re.I), ["推荐系统"]),
    (re.compile(r"collaborative\s+filtering", re.I), ["协同过滤", "推荐系统"]),
    (re.compile(r"sequence\s+recommendation", re.I), ["序列推荐", "推荐系统"]),
    (re.compile(r"time\s+series", re.I), ["时间序列", "时序分析"]),
    (re.compile(r"spatio[\s-]?temporal", re.I), ["时空数据", "时空数据分析"]),
    (re.compile(r"trajectory\s+data", re.I), ["轨迹数据", "轨迹数据挖掘"]),
    (re.compile(r"stream\s+data", re.I), ["流数据", "流数据处理"]),
    (re.compile(r"data\s+cleaning", re.I), ["数据清洗"]),
    (re.compile(r"distributed\s+database", re.I), ["分布式数据库", "分布式数据管理"]),
    # 系统 / 网络 / 安全
    (re.compile(r"software\s+engineering", re.I), ["软件工程", "可信软件"]),
    (re.compile(r"operating\s+system", re.I), ["操作系统", "系统软件"]),
    (re.compile(r"distributed\s+system", re.I), ["分布式系统", "分布式计算"]),
    (re.compile(r"cloud\s+computing", re.I), ["云计算"]),
    (re.compile(r"edge\s+computing", re.I), ["边缘计算", "移动边缘计算"]),
    (re.compile(r"internet\s+of\s+things", re.I), ["物联网"]),
    (re.compile(r"industrial\s+internet", re.I), ["工业互联网"]),
    (re.compile(r"wireless\s+communication", re.I), ["无线通信"]),
    (re.compile(r"wireless\s+network", re.I), ["无线网络", "无线通信"]),
    (re.compile(r"mobile\s+edge", re.I), ["移动边缘计算", "边缘计算"]),
    (re.compile(r"software\s+defined\s+network", re.I), ["软件定义网络"]),
    (re.compile(r"network\s+security", re.I), ["网络安全", "网络与信息安全"]),
    (re.compile(r"information\s+security", re.I), ["信息安全", "网络安全"]),
    (re.compile(r"cyber\s+security", re.I), ["网络安全", "信息安全"]),
    (re.compile(r"block\s*chain", re.I), ["区块链"]),
    (re.compile(r"human[\s-]?computer\s+interaction", re.I), ["人机交互", "智能人机交互"]),
    (re.compile(r"virtual\s+reality", re.I), ["虚拟现实"]),
    (re.compile(r"augmented\s+reality", re.I), ["增强现实"]),
    (re.compile(r"high\s+performance\s+computing", re.I), ["高性能计算", "并行计算"]),
    (re.compile(r"parallel\s+computing", re.I), ["并行计算", "高性能计算"]),
    # 通信 / 电子 / 光学
    (re.compile(r"signal\s+processing", re.I), ["信号处理", "智能信号处理"]),
    (re.compile(r"digital\s+signal\s+processing", re.I), ["数字信号处理", "信号处理"]),
    (re.compile(r"optical\s+communication", re.I), ["光通信", "光纤通信"]),
    (re.compile(r"fiber\s+optics", re.I), ["光纤通信", "光纤"]),
    (re.compile(r"wireless\s+channel", re.I), ["无线信道", "信道估计"]),
    (re.compile(r"channel\s+estimation", re.I), ["信道估计", "无线通信"]),
    (re.compile(r"modulation\s+classification", re.I), ["调制分类", "通信信号处理"]),
    (re.compile(r"antenna\s+design", re.I), ["天线设计", "天线"]),
    (re.compile(r"integrated\s+circuit", re.I), ["集成电路", "集成电路设计"]),
    (re.compile(r"microelectronics", re.I), ["微电子", "微电子器件"]),
    (re.compile(r"semiconductor", re.I), ["半导体", "半导体器件"]),
    (re.compile(r"photovoltaic", re.I), ["光伏", "太阳能电池"]),
    (re.compile(r"solar\s+cell", re.I), ["太阳电池", "太阳能电池"]),
    (re.compile(r"quantum\s+computing", re.I), ["量子计算", "量子信息"]),
    (re.compile(r"quantum\s+information", re.I), ["量子信息", "量子通信"]),
    # 机器人 / 控制
    (re.compile(r"intelligent\s+robot", re.I), ["智能机器人", "机器人"]),
    (re.compile(r"robot\s+control", re.I), ["机器人控制", "智能控制"]),
    (re.compile(r"autonomous\s+control", re.I), ["自主控制", "智能控制"]),
    (re.compile(r"intelligent\s+control", re.I), ["智能控制", "智能控制与应用"]),
    # 生物 / 医学
    (re.compile(r"bioinformatics", re.I), ["生物信息学", "生物信息"]),
    (re.compile(r"computational\s+biology", re.I), ["计算生物学", "生物信息学"]),
    (re.compile(r"systems\s+biology", re.I), ["系统生物学", "生物信息学"]),
    (re.compile(r"medical\s+informatics", re.I), ["医学信息", "医学信息学"]),
    (re.compile(r"protein\s+interaction", re.I), ["蛋白质相互作用", "生物信息"]),
    # 数学
    (re.compile(r"partial\s+differential\s+equation", re.I), ["偏微分方程", "数值方法"]),
    (re.compile(r"numerical\s+method", re.I), ["数值方法", "数值计算"]),
    (re.compile(r"optimization\s+theory", re.I), ["最优化理论", "优化算法"]),
    (re.compile(r"convex\s+optimization", re.I), ["凸优化", "最优化"]),
]


def _normalize_query_input(query: str) -> str:
    text = (query or "").strip()
    text = re.sub(r"[-_/]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_field_prefix(query: str) -> Tuple[str, str | None]:
    field_map = [
        ("姓名:", "name"),
        ("name:", "name"),
        ("论文:", "papers"),
        ("paper:", "papers"),
        ("研究方向:", "research"),
        ("research:", "research"),
    ]
    for prefix, fname in field_map:
        if query.lower().startswith(prefix.lower()):
            return query[len(prefix) :].strip(), fname
    return query, None


def _build_query_plan(query: str) -> QueryPlan:
    """将英文缩写/短语扩展为中文检索词，与原始查询一并参与匹配。"""
    original = _normalize_query_input(query)
    if not original:
        return QueryPlan("", [], [])

    zh_phrases: List[str] = []
    lower = original.lower()

    for pattern, aliases in _EN_PHRASE_RULES:
        if pattern.search(lower):
            zh_phrases.extend(aliases)

    for word in re.findall(r"[a-zA-Z]+", original):
        aliases = _ABBR_ALIASES.get(word.lower())
        if aliases:
            zh_phrases.extend(aliases)

    phrases: List[str] = []
    seen_phrase = set()
    for item in zh_phrases + [original]:
        key = _normalize_text(item)
        if key and key not in seen_phrase:
            seen_phrase.add(key)
            phrases.append(item)

    tokens: List[str] = []
    seen_token = set()
    for phrase in phrases:
        for term in _relax_terms(phrase):
            key = _normalize_text(term)
            if key and key not in seen_token:
                seen_token.add(key)
                tokens.append(term)

    return QueryPlan(original=original, phrases=phrases, tokens=tokens)


def expand_query(query: str) -> List[str]:
    """返回包含跨语言扩展在内的全部检索短语。"""
    cleaned, _ = _strip_field_prefix((query or "").strip())
    return _build_query_plan(cleaned).phrases


def query_matches_text(text: str, query: str) -> bool:
    """判断文本是否命中查询（含英文缩写/短语的中文扩展）。"""
    if not (query or "").strip():
        return True
    if not text:
        return False
    cleaned, _ = _strip_field_prefix((query or "").strip())
    plan = _build_query_plan(cleaned)
    hay = text.casefold()
    for phrase in plan.phrases:
        if phrase.casefold() in hay:
            return True
    for term in plan.tokens:
        if len(term) >= 2 and term.casefold() in hay:
            return True
    return False


def _stable_sort_key(result: SearchResult) -> Tuple[float, str, str, str, str]:
    teacher = result.teacher
    return (
        -result.score,
        teacher.name or "",
        teacher.department or "",
        teacher.url or "",
        result.doc.doc_id or "",
    )


def _dedupe_and_rank(results: List[SearchResult], top_k: int) -> List[SearchResult]:
    if not results:
        return []

    merged: Dict[Tuple[str, str], SearchResult] = {}
    for item in results:
        teacher = item.teacher
        key = (
            (teacher.name or "").replace(" ", ""),
            (teacher.department or "").strip(),
        )
        existing = merged.get(key)
        if existing is None or item.score > existing.score:
            merged[key] = item
            continue

        if item.score == existing.score:
            # Keep deterministic output when scores tie.
            if _stable_sort_key(item) < _stable_sort_key(existing):
                merged[key] = item

    ranked = list(merged.values())
    ranked.sort(key=_stable_sort_key)
    return ranked[:top_k]


def load_teachers(path: str) -> List[TeacherRecord]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    teachers: List[TeacherRecord] = []
    for item in raw:
        kw_raw = item.get("keywords") or []
        profile_keywords: List[str] = []
        if isinstance(kw_raw, list):
            for kw in kw_raw:
                s = re.sub(r"\s+", " ", str(kw)).strip()
                if s and s not in profile_keywords:
                    profile_keywords.append(s)

        papers_struct: List[dict] = []
        struct_raw = item.get("papers_struct") or []
        if isinstance(struct_raw, list):
            for row in struct_raw:
                if isinstance(row, dict) and row.get("title"):
                    papers_struct.append(row)

        teachers.append(
            TeacherRecord(
                name=(item.get("name") or "").strip(),
                department=(item.get("department") or "").strip(),
                career=(item.get("career") or "").strip(),
                url=(item.get("cn_url") or item.get("url") or "").strip(),
                research_direction=(item.get("research_direction") or "").strip(),
                personal_intro=(item.get("personal_intro") or "").strip(),
                papers_text=(item.get("papers_text") or "").strip(),
                profile_keywords=profile_keywords,
                papers_struct=papers_struct,
            )
        )
    return teachers


# Navigation / section-header lines that appear on every crawled page and are
# pure noise for snippets and the index.
_NAV_LINES = frozenset(
    {
        "教师个人主页",
        "English",
        "返回首页",
        "欢迎登录",
        "导航",
        "个人资料",
        "个人概况",
        "研究领域",
        "研究方向",
        "开授课程",
        "科研项目",
        "论文",
        "科研成果",
        "荣誉及奖励",
        "招生信息",
        "相关教师",
        "最新更新",
        "教育经历",
        "工作经历",
        "社会职务",
        "个人简介",
        "访问",
        "科学研究",
        "基本信息",
        "联系方式",
        "教学",
        "课程",
        "科研团队",
    }
)


def _clean_corpus_text(raw: str) -> str:
    """Drop crawler header, page template/navigation, label-only and noise lines.

    Each corpus file starts with a `key: value` header terminated by a `---`
    separator, followed by the page body. The body still contains the site's
    navigation menu, visit counters and empty `label：` rows, which leak into
    snippets (e.g. "返回首页 欢迎登录 导航"). We strip those so snippets and the
    index only keep substantive content.
    """
    if not raw:
        return ""
    parts = re.split(r"\n-{3,}\n", raw, maxsplit=1)
    body = parts[1] if len(parts) > 1 else raw

    kept: List[str] = []
    prev = None
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        if s in _NAV_LINES:
            continue
        if s.startswith(("姓名:", "页面标题:")):
            continue
        # Visit counters / stray numeric lines.
        if re.fullmatch(r"\d{1,6}", s):
            continue
        # Label-only rows like "联系电话：" / "学位：" / "研究方向：" (no value).
        if len(s) <= 12 and s.endswith(("：", ":")):
            continue
        # Collapse consecutive duplicate lines (some pages repeat sections).
        if s == prev:
            continue
        kept.append(s)
        prev = s
    return "\n".join(kept)


def load_corpus(corpus_dir: str) -> List[DocRecord]:
    docs: List[DocRecord] = []
    for filename in os.listdir(corpus_dir):
        if not filename.endswith(".txt"):
            continue
        path = os.path.join(corpus_dir, filename)
        with open(path, "r", encoding="utf-8") as f:
            text = _clean_corpus_text(f.read())
        doc_id = os.path.splitext(filename)[0]
        docs.append(DocRecord(doc_id=doc_id, path=path, text=text))
    return docs


def _tokenize(text: str) -> List[str]:
    tokens: List[str] = []
    tokens.extend(re.findall(r"[a-zA-Z0-9]+", text.lower()))

    cjk_blocks = re.findall(r"[\u4e00-\u9fff]+", text)
    for block in cjk_blocks:
        if not block:
            continue
        tokens.extend(list(block))
        if len(block) > 1:
            tokens.extend(block[i : i + 2] for i in range(len(block) - 1))
    return tokens


def _build_teacher_lookup(teachers: Iterable[TeacherRecord]) -> Dict[str, List[TeacherRecord]]:
    lookup: Dict[str, List[TeacherRecord]] = defaultdict(list)
    for teacher in teachers:
        key = teacher.name.replace(" ", "")
        if key:
            lookup[key].append(teacher)
    return lookup


def _mask_private(text: str) -> str:
    if not text:
        return text
    # Email: ASCII local part + domain with a real TLD. This avoids masking
    # things like "周 国栋@Google Scholar" (no dotted TLD) as a fake email.
    text = re.sub(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        "***@***",
        text,
    )
    # Phone: 11-digit mobiles or separated landlines only, so grant codes like
    # "#61331011" and year ranges like "2014.01-2018.12" are left intact.
    text = re.sub(
        r"(?<![\d#-])(?:1[3-9]\d{9}|0\d{2,3}[-\s]?\d{7,8}|\d{3,4}[-\s]\d{7,8})(?!\d)",
        "***",
        text,
    )
    return text


def _extract_snippet(text: str, query_tokens: List[str], limit: int = 120) -> str:
    if not text:
        return ""
    for token in query_tokens:
        if not token:
            continue
        idx = text.find(token)
        if idx != -1:
            start = max(0, idx - 40)
            end = min(len(text), idx + 40)
            snippet = text[start:end].replace("\n", " ").strip()
            return snippet[:limit]
    snippet = text[:limit].replace("\n", " ").strip()
    return snippet


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", "", text).casefold()


_BOILERPLATE_MARKERS = (
    "基本信息",
    "社会职务",
    "联系方式",
    "科学研究",
    "科研团队",
    "研究项目",
    "主要项目",
    "国家级科研项目",
    "最近更新",
    "论文发表",
    "代表性论文",
    "近五年",
    "课题组",
    "教学",
    "主持",
    "招生信息",
    "荣誉及奖励",
    "开授课程",
    "版权所有",
    "技术支持",
    "Copyright",
    "职称：",
    "-----",
)

_SECTION_LABELS = ("研究领域:", "研究方向:", "个人简介:", "简介:", "论文:", "论文/成果:")

_FOOTER_MARKERS = (
    "版权所有",
    "技术支持",
    "Copyright",
    "招生信息",
    "荣誉及奖励",
    "开授课程",
)


def _trim_footer(text: str) -> str:
    if not text:
        return ""
    cut = len(text)
    for marker in _FOOTER_MARKERS:
        idx = text.find(marker)
        if 0 < idx < cut:
            cut = idx
    return text[:cut]


def _clean_field(text: str, max_len: int = 180, cut_boilerplate: bool = False) -> str:
    """Collapse whitespace, optionally cut crawler boilerplate, and truncate."""
    if not text:
        return ""
    if cut_boilerplate:
        cut = len(text)
        for marker in _BOILERPLATE_MARKERS:
            idx = text.find(marker)
            if 0 < idx < cut:
                cut = idx
        text = text[:cut]
    text = re.sub(r"\s+", " ", text).strip(" 、；;,，.-")
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "…"
    return text


def _strip_section_label(text: str) -> str:
    for label in _SECTION_LABELS:
        if text.startswith(label):
            return text[len(label) :].strip()
    return text


def _query_terms(query: str) -> List[str]:
    cleaned, _ = _strip_field_prefix((query or "").strip())
    plan = _build_query_plan(cleaned)
    out: List[str] = []
    seen = set()
    for term in plan.phrases + plan.tokens:
        if term and term not in seen:
            out.append(term)
            seen.add(term)
    return out


def _match_keywords(query: str, teacher: TeacherRecord) -> List[str]:
    haystack = _normalize_text(
        " ".join(
            [
                teacher.name,
                teacher.department,
                teacher.research_direction,
                teacher.papers_text,
                teacher.personal_intro,
            ]
        )
    )
    matched = []
    for term in _query_terms(query):
        if _normalize_text(term) in haystack and term not in matched:
            matched.append(term)
    # Keep only the longest non-overlapping matches so relax sub-grams
    # like "周国"/"栋" do not clutter the display alongside "周国栋".
    matched.sort(key=lambda x: -len(x))
    kept: List[str] = []
    for term in matched:
        if not any(term in longer for longer in kept):
            kept.append(term)
    return kept[:5]


def _clean_snippet(snippet: str) -> str:
    text = _strip_section_label(re.sub(r"\s+", " ", snippet or "").strip())
    cut = len(text)
    for marker in _BOILERPLATE_MARKERS:
        idx = text.find(marker)
        if 0 < idx < cut:
            cut = idx
    text = text[:cut].strip(" -—、；;,，.·|")
    # Drop fragments that carry no real information (e.g. "务。", "：").
    if len(re.sub(r"[\s\W]+", "", text)) < 4:
        return ""
    return text


_INCOMPLETE_VALUES = frozenset({"研究方向", "研究领域", "研究兴趣", "简介", "个人简介"})


def _looks_incomplete(value: str) -> bool:
    """Detect dangling labels / lead-in headings with no real content.

    Crawling sometimes captures only a heading such as "研究方向：" or
    "我近期的研究兴趣包括：" while the actual list was rendered elsewhere. Such a
    value should be hidden instead of shown as a useless field.
    """
    stripped = (value or "").strip()
    if not stripped:
        return True
    if stripped.endswith(("：", ":")):
        return True
    core = stripped.rstrip("：: 。.").strip()
    if core in _INCOMPLETE_VALUES:
        return True
    return len(core) < 2


_CCF_A_HINTS = (
    "sigir",
    "acl",
    "kdd",
    "icde",
    "infocom",
    "cvpr",
    "iccv",
    "eccv",
    "neurips",
    "nips",
    "icml",
    "aaai",
    "ijcai",
    "usenix atc",
    "sosp",
    "osdi",
    "nsdi",
    "ccs",
    "usenix security",
    "oakland",
    "sp ",
    "ieee tdsc",
    "ieee tifs",
    "tkde",
    "tocs",
    "tpds",
    "tods",
    "软件学报",
    "计算机学报",
)
_CCF_B_HINTS = (
    "www",
    "emnlp",
    "coling",
    "cikm",
    "icdm",
    "wsdm",
    "recsys",
    "mm ",
    "icassp",
    "icse",
    "fse",
    "ase",
    "icnp",
    "imc",
    "sigmod",
    "vldb",
    "pods",
    "iclr",
    "naacl",
    "tkde",
    "tmm",
    "tcsvt",
)
_CCF_C_HINTS = (
    "icann",
    "iconip",
    "pakdd",
    "dasfaa",
    "trustcom",
    "icpads",
    "hpca",
    "micro",
    "isca",
)


def _infer_ccf_rank(venue: str, title: str = "") -> str:
    hay = f"{venue} {title}".lower()
    for hint in _CCF_A_HINTS:
        if hint in hay:
            return "A"
    for hint in _CCF_B_HINTS:
        if hint in hay:
            return "B"
    for hint in _CCF_C_HINTS:
        if hint in hay:
            return "C"
    return ""


_RD_DISPLAY_META_RE = re.compile(
    r"(教授|副教授|讲师|助教|https?://|dblp\.)",
    re.I,
)
_RD_DISPLAY_SKIP_RE = re.compile(
    r"(国家级|省部级|科研项目|主持人|合作者|NSFC|国家自然科学基金|"
    r"重大研究计划|培育项目|课题|获批|立项|特聘|人才引进)",
    re.I,
)
_RD_TAG_NOISE_RE = re.compile(
    r"^(苏州大学|苏大|东南大学|山东大学|浙江大学|北京大学|清华大学|"
    r".*大学|.*学院|硕士研究生?|博士研究生?|.*硕士学位|.*博士学位|"
    r"讲师|副教授|教授|助教|硕导|博导|硕士|博士|个人信息|成果奖励|教学招生|"
    r"荣誉奖励|科研成果)$",
    re.I,
)


def _is_noise_research_tag(tag: str, teacher_name: str = "", department: str = "") -> bool:
    t = re.sub(r"\s+", " ", (tag or "").strip())
    if not t or len(t) < 2:
        return True
    name = (teacher_name or "").replace(" ", "")
    tc = t.replace(" ", "")
    if name:
        if t == teacher_name or tc == name:
            return True
        if name in tc and len(t) <= len(teacher_name) + 6:
            return True
    if _RD_TAG_NOISE_RE.match(t):
        return True
    if re.search(r"(大学|版权|技术支持|信箱|招生|Copyright)", t) and len(t) <= 16:
        return True
    if department and t in department:
        return True
    return False


def _normalize_research_display(
    text: str,
    limit: int = 140,
    teacher_name: str = "",
    department: str = "",
) -> str:
    """展示前规范化研究方向：拆编号/换行/括号，去掉明显噪音。"""
    if not text:
        return ""
    for cut in ("http://", "https://", "dblp."):
        idx = text.lower().find(cut)
        if idx > 0:
            text = text[:idx]
    text = re.sub(r"\(\s*[\r\n]+\s*", "(", text)
    text = re.sub(r"[\r\n]+\s*\)", ")", text)
    tags: List[str] = []
    seen: set[str] = set()
    for raw_line in re.split(r"[\r\n]+", text):
        line = raw_line.strip()
        if not line or _RD_DISPLAY_META_RE.search(line) or _RD_DISPLAY_SKIP_RE.search(line):
            continue
        line = re.sub(r"^[\d一二三四五六七八九十]+[\.、．:：]\s*", "", line)
        line = re.sub(r"^[（(]\d+[）)]\s*", "", line)
        line = re.sub(
            r"[（(]([^（）()]*)[）)]",
            lambda m: ("、" + m.group(1).replace(";", "、").replace(",", "、")) if m.group(1).strip() else "",
            line,
        )
        line = re.sub(r"[（()）]", "、", line)
        for part in re.split(r"[;；、,/|]+", line):
            s = re.sub(r"\s+", " ", part).strip(" ：:-.等。")
            if s.endswith("等"):
                s = s[:-1].strip(" 、；;，,.")
            if (
                len(s) < 2
                or _RD_DISPLAY_META_RE.search(s)
                or _RD_DISPLAY_SKIP_RE.search(s)
                or _is_noise_research_tag(s, teacher_name, department)
                or s in seen
            ):
                continue
            seen.add(s)
            tags.append(s)
    return "、".join(tags[:10])[:limit]


def _split_research_tags(
    text: str, limit: int = 8, teacher_name: str = "", department: str = ""
) -> List[str]:
    normalized = _normalize_research_display(
        text, limit=200, teacher_name=teacher_name, department=department
    )
    if not normalized:
        return []
    parts = re.split(r"[、；;，,/|]+", normalized)
    tags: List[str] = []
    for part in parts:
        s = re.sub(r"\s+", " ", part).strip(" ：:-")
        if len(s) < 2 or s in tags:
            continue
        tags.append(s)
    return tags[:limit]


_PAPER_LABEL_ONLY = frozenset({
    "专利", "软件著作", "著作", "专利、软件著作", "待更新", "暂无", "无",
})
_PAPER_PLACEHOLDER_HINTS = ("待更新", "请见", "标签页", "没有维护", "旧版")
_PAPER_PROFILE_RE = re.compile(
    r"(人才引进|特聘教授|博士生导师|实验室|主任|电话[:：]|苏州大学|@|特聘)",
    re.I,
)
_PAPER_LINE_NOISE_RE = re.compile(
    r"(国家自然|自然科学基金|基金项目|面上项目|重点项目|子项目|产学研|科技支撑|科技计划|"
    r"项目负责人|排名第二|招生要求|培养方向|优势条件|科研补助|青年基金|"
    r"精品.*课程|操作系统原理|Linux操作系统|工程经济与|程序设计|课程实践|"
    r"科技进步|二等奖|三等奖|优秀奖|指导学生|创新项目|"
    r"审稿人|审稿编辑|Reviewer|Area Chair|TPC member|编委|副主编|"
    r"SCI/EI收录|余篇|发明专利|授权专利|教材|学术著作|"
    r"自然科学研究项目|目前主要研究方向|主要讲授的课程|"
    r"Frontiers in Communication|Electronic Letters)",
    re.I,
)
_JOURNAL_ABBR_ONLY_RE = re.compile(
    r"^(?:IEEE\s*|ACM\s*)?[A-Z][A-Za-z.]{1,10}"
    r"(?:\s*[,，、]\s*(?:IEEE\s*|ACM\s*)?[A-Z][A-Za-z.]{1,10})+$",
)


def _normalize_paper_cmp(s: str) -> str:
    return re.sub(r"\s+", "", s.replace("，", "、").replace(",", "、"))


def _is_paper_line_noise(
    title: str,
    teacher_name: str = "",
    research_direction: str = "",
) -> bool:
    t = re.sub(r"\s+", " ", (title or "")).strip()
    if not t:
        return True
    if _PAPER_LINE_NOISE_RE.search(t):
        return True
    if re.search(
        r"概论|分析与设计|测试与质量|需求工程|蓝桥杯|招生信息|优秀指导教师|发邮件时|"
        r"学生具体情况|联系方式如下|本组招生|协助学生|攻读博士|不打扰学生",
        t,
    ):
        return True
    if re.search(r"国际学术刊物|国际学术会议包括|包括ACM TKDD", t):
        return True
    if re.fullmatch(r"[\u4e00-\u9fff、，；;：:\s]{4,45}研究", t) and not re.search(r"[A-Za-z]{4,}", t):
        return True
    if re.fullmatch(r"\(?\d*\)?:?\s*\d{5,8}\s*\(\d{4}\)|\d{5,8}\s*\(\d{4}\)", t):
        return True
    if teacher_name and teacher_name in t and re.search(r"基金|项目|,20\d{2}", t):
        return True
    if research_direction:
        if _normalize_paper_cmp(t) in _normalize_paper_cmp(research_direction):
            return True
        rd_tags = {
            _normalize_paper_cmp(x)
            for x in re.split(r"[、，,;；]+", research_direction)
            if x.strip()
        }
        line_tags = [
            _normalize_paper_cmp(x)
            for x in re.split(r"[、，,;；]+", t)
            if x.strip()
        ]
        if len(line_tags) >= 2:
            overlap = sum(1 for tag in line_tags if tag in rd_tags)
            if overlap >= max(2, int(len(line_tags) * 0.75)):
                return True
    if _JOURNAL_ABBR_ONLY_RE.match(t):
        return True
    if re.search(r"的审稿人|余篇|收录论文|发表论文\d|论文\d+余", t):
        return True
    if re.search(r"(20\d{2}\.\d{2}-20\d{2}|计\d{2}计算机|,\d{4}/\d{2},)", t):
        return True
    if (
        re.search(r"^(?:Frontiers|Journal of|IEEE|ACM|Proceedings|Trans\.|Comput\.)", t, re.I)
        and ":" not in t
        and len(t) < 80
    ):
        return True
    return False


def _is_displayable_paper(
    title: str,
    teacher_name: str = "",
    research_direction: str = "",
) -> bool:
    s = re.sub(r"\s+", "", title or "").strip(" 、；;，,.-")
    if not s or s in _PAPER_LABEL_ONLY:
        return False
    if _is_paper_line_noise(title, teacher_name, research_direction):
        return False
    if _PAPER_PROFILE_RE.search(title or ""):
        return False
    if len(s) < 8 and not re.search(r"[A-Za-z]{4,}", s):
        return False
    if re.search(r":\s*[A-Z][a-z]{4,}", title or ""):
        return len(title or "") >= 30
    if re.search(r"[A-Za-z]{4,}", title or ""):
        return (title or "").count(" ") >= 2 and len(title or "") >= 20
    return not any(h in s for h in _PAPER_PLACEHOLDER_HINTS)


def _parse_paper_dict_line(line: str) -> PaperItem | None:
    """兼容 papers_text 里误存的 Python dict 字符串。"""
    s = line.strip()
    if not s.startswith("{") or "venue" not in s:
        return None
    try:
        row = ast.literal_eval(s)
    except (SyntaxError, ValueError):
        return None
    if not isinstance(row, dict):
        return None
    title = re.sub(r"\s+", " ", str(row.get("title") or "")).strip()
    venue = re.sub(r"\s+", " ", str(row.get("venue") or "")).strip()
    year = re.sub(r"\D", "", str(row.get("year") or ""))[:4]
    if not title and venue:
        title = f"{venue} ({year})" if year else venue
    if not title:
        return None
    rank = str(row.get("ccf_rank") or "").strip().upper()
    if rank not in {"A", "B", "C"}:
        rank = _infer_ccf_rank(venue, title)
    return PaperItem(title=title, venue=venue, year=year, ccf_rank=rank)


def _build_paper_items(teacher: TeacherRecord, papers_fallback: str) -> List[PaperItem]:
    items: List[PaperItem] = []
    if teacher.papers_struct:
        for row in teacher.papers_struct[:12]:
            title = re.sub(r"\s+", " ", str(row.get("title") or "")).strip()
            venue = re.sub(r"\s+", " ", str(row.get("venue") or "")).strip()
            year = re.sub(r"\D", "", str(row.get("year") or ""))[:4]
            if not title and venue:
                title = f"{venue} ({year})" if year else venue
            if not title:
                continue
            if _is_paper_line_noise(title, teacher.name, teacher.research_direction):
                continue
            venue = re.sub(r"\s+", " ", str(row.get("venue") or "")).strip()
            year = re.sub(r"\D", "", str(row.get("year") or ""))[:4]
            rank = str(row.get("ccf_rank") or "").strip().upper()
            if rank not in {"A", "B", "C"}:
                rank = _infer_ccf_rank(venue, title)
            items.append(PaperItem(title=title, venue=venue, year=year, ccf_rank=rank))
        if items:
            return items

    for line in re.split(r"[\r\n]+", papers_fallback or ""):
        parsed = _parse_paper_dict_line(line)
        if parsed:
            items.append(parsed)
            continue
        title = re.sub(r"\s+", " ", line).strip()
        if not _is_displayable_paper(title, teacher.name, teacher.research_direction):
            continue
        rank = _infer_ccf_rank("", title)
        items.append(PaperItem(title=title, ccf_rank=rank))
    return items[:12]


def build_display(result: SearchResult, rank: int, query: str = "") -> DisplayResult:
    """Build a clean, de-duplicated view model shared by CLI and GUI."""
    teacher = result.teacher
    research_raw = (
        "" if _looks_incomplete(teacher.research_direction) else teacher.research_direction
    )
    research = _mask_private(
        _normalize_research_display(
            _clean_field(research_raw, max_len=500, cut_boilerplate=True),
            limit=140,
            teacher_name=teacher.name,
            department=teacher.department,
        )
    )
    intro_raw = "" if _looks_incomplete(teacher.personal_intro) else teacher.personal_intro
    papers_raw = "" if _looks_incomplete(teacher.papers_text) else teacher.papers_text
    intro = _mask_private(_clean_field(_trim_footer(intro_raw), max_len=200))
    papers = _mask_private(_clean_field(_trim_footer(papers_raw), max_len=200))

    snippet = _mask_private(_clean_snippet(result.snippet))

    visible = _normalize_text(" ".join([research, intro, papers]))
    snippet_norm = _normalize_text(snippet)
    if snippet_norm and visible and snippet_norm in visible:
        snippet = ""

    research_tags = _split_research_tags(
        research, teacher_name=teacher.name, department=teacher.department
    )
    papers_source = _mask_private(_trim_footer(teacher.papers_text or papers_raw))
    paper_items = _build_paper_items(teacher, papers_source)

    return DisplayResult(
        rank=rank,
        name=teacher.name,
        department=teacher.department,
        career=teacher.career,
        research=research,
        intro=intro,
        papers=papers,
        snippet=snippet,
        url=teacher.url,
        score=result.score,
        keywords=_match_keywords(query, teacher),
        paper_items=paper_items,
        research_tags=research_tags,
        profile_keywords=list(teacher.profile_keywords),
    )


def _relax_terms(query: str) -> List[str]:
    terms: List[str] = []
    terms.extend(re.findall(r"[a-zA-Z0-9]+", query.lower()))

    cjk_blocks = re.findall(r"[\u4e00-\u9fff]+", query)
    for block in cjk_blocks:
        if not block:
            continue
        if len(block) <= 2:
            terms.append(block)
            continue
        terms.extend(block[i : i + 2] for i in range(0, len(block), 2))

    deduped: List[str] = []
    seen = set()
    for term in terms:
        if term and term not in seen:
            deduped.append(term)
            seen.add(term)
    return deduped


def build_index(docs: List[DocRecord]) -> Tuple[Dict[str, Dict[str, int]], Dict[str, float]]:
    inverted: Dict[str, Dict[str, int]] = defaultdict(dict)
    doc_freq: Dict[str, int] = defaultdict(int)

    for doc in docs:
        tf = Counter(_tokenize(doc.text))
        for term, freq in tf.items():
            inverted[term][doc.doc_id] = freq
        for term in tf.keys():
            doc_freq[term] += 1

    num_docs = max(len(docs), 1)
    idf: Dict[str, float] = {}
    for term, df in doc_freq.items():
        idf[term] = math.log(1 + num_docs / (1 + df))

    doc_norms: Dict[str, float] = defaultdict(float)
    for term, postings in inverted.items():
        term_idf = idf.get(term, 0.0)
        for doc_id, freq in postings.items():
            weight = (1 + math.log(freq)) * term_idf
            doc_norms[doc_id] += weight * weight

    for doc_id, value in doc_norms.items():
        doc_norms[doc_id] = math.sqrt(value) if value > 0 else 1.0

    return inverted, doc_norms


def _phrase_search(
    query: str,
    docs: List[DocRecord],
    teachers: List[TeacherRecord],
    top_k: int,
) -> List[SearchResult]:
    if not query:
        return []

    needle = _normalize_text(query)
    if not needle:
        return []

    query_tokens = _tokenize(query)
    results: List[SearchResult] = []
    for doc in docs:
        haystack = _normalize_text(doc.text)
        if needle and needle in haystack:
            count = haystack.count(needle)
            score = 1.0 + math.log(1 + count)
            teacher = next((t for t in teachers if t.name and t.name in doc.path), None)
            if not teacher:
                continue
            snippet = _extract_snippet(doc.text, query_tokens or [query])
            results.append(SearchResult(score=score, doc=doc, teacher=teacher, snippet=snippet))

    for teacher in teachers:
        haystack = _normalize_text(
            " ".join(
                [
                    teacher.name,
                    teacher.department,
                    teacher.career,
                    teacher.research_direction,
                    teacher.personal_intro,
                    teacher.papers_text,
                ]
            )
        )
        if needle and needle in haystack:
            doc = next((d for d in docs if teacher.name and teacher.name in d.path), None)
            if not doc:
                doc = DocRecord(doc_id=teacher.name, path="", text=teacher.personal_intro)
            snippet = _extract_snippet(doc.text, query_tokens or [query])
            results.append(SearchResult(score=1.0, doc=doc, teacher=teacher, snippet=snippet))

    return _dedupe_and_rank(results, top_k)


def _token_search(
    query_tokens: List[str],
    docs: List[DocRecord],
    teachers: List[TeacherRecord],
    inverted: Dict[str, Dict[str, int]],
    doc_norms: Dict[str, float],
    top_k: int,
    require_all: bool = False,
) -> List[SearchResult]:
    if not query_tokens:
        return []

    doc_ids: Iterable[str]
    if require_all:
        postings_lists = [inverted.get(term) for term in query_tokens if term]
        if not postings_lists or any(postings is None for postings in postings_lists):
            return []
        doc_ids = set(postings_lists[0].keys())
        for postings in postings_lists[1:]:
            doc_ids = set(doc_ids).intersection(postings.keys())
        if not doc_ids:
            return []
    else:
        doc_ids = []

    scores: Dict[str, float] = defaultdict(float)
    for term in query_tokens:
        postings = inverted.get(term)
        if not postings:
            continue
        for doc_id, tf in postings.items():
            if require_all and doc_id not in doc_ids:
                continue
            weight = 1 + math.log(tf)
            scores[doc_id] += weight

    results: List[SearchResult] = []
    doc_map = {doc.doc_id: doc for doc in docs}
    for doc_id, score in scores.items():
        norm = doc_norms.get(doc_id, 1.0)
        final_score = score / norm
        doc = doc_map.get(doc_id)
        if not doc:
            continue
        teacher = next((t for t in teachers if t.name and t.name in doc.path), None)
        if not teacher:
            continue
        snippet = _extract_snippet(doc.text, query_tokens)
        results.append(SearchResult(score=final_score, doc=doc, teacher=teacher, snippet=snippet))

    return _dedupe_and_rank(results, top_k)


def _fuzzy_search(
    query: str,
    docs: List[DocRecord],
    teachers: List[TeacherRecord],
    top_k: int,
    threshold: int = 70,
) -> List[SearchResult]:
    if not _FUZZY_AVAILABLE or not query:
        return []

    query_tokens = _tokenize(query)
    results: List[SearchResult] = []
    for teacher in teachers:
        haystack = " ".join(
            [
                teacher.name,
                teacher.department,
                teacher.career,
                teacher.research_direction,
                teacher.personal_intro,
                teacher.papers_text,
            ]
        )
        if not haystack.strip():
            continue
        score = fuzz.partial_ratio(query, haystack)
        if score < threshold:
            continue
        doc = next((d for d in docs if teacher.name and teacher.name in d.path), None)
        if not doc:
            doc = DocRecord(doc_id=teacher.name, path="", text=teacher.personal_intro)
        snippet = _extract_snippet(doc.text, query_tokens or [query])
        results.append(SearchResult(score=float(score), doc=doc, teacher=teacher, snippet=snippet))

    return _dedupe_and_rank(results, top_k)


def _field_search(
    plan: QueryPlan,
    field: str,
    docs: List[DocRecord],
    teachers: List[TeacherRecord],
    top_k: int,
) -> List[SearchResult]:
    """Search restricted to a single teacher field (papers / research)."""
    if not plan.original:
        return []

    results: List[SearchResult] = []
    for teacher in teachers:
        field_text = (
            teacher.papers_text if field == "papers" else teacher.research_direction
        )
        haystack = _normalize_text(field_text)
        if not haystack:
            continue
        score = 0.0
        for phrase in plan.phrases:
            norm = _normalize_text(phrase)
            if norm and norm in haystack:
                score += 3.0 + 0.2 * len(norm)
        for term in plan.tokens:
            norm = _normalize_text(term)
            if norm and len(norm) >= 2 and norm in haystack:
                score += 1.0
        if score <= 0:
            continue
        doc = next((d for d in docs if teacher.name and teacher.name in d.path), None)
        if not doc:
            doc = DocRecord(doc_id=teacher.name, path="", text=field_text)
        snippet = _extract_snippet(field_text, plan.phrases + plan.tokens)
        results.append(SearchResult(score=score, doc=doc, teacher=teacher, snippet=snippet))

    return _dedupe_and_rank(results, top_k)


def search(
    query: str,
    docs: List[DocRecord],
    teachers: List[TeacherRecord],
    inverted: Dict[str, Dict[str, int]],
    doc_norms: Dict[str, float],
    top_k: int = 8,
    allow_relax: bool = True,
    enable_fuzzy: bool = True,
    fuzzy_threshold: int = 70,
) -> List[SearchResult]:
    raw_query = (query or "").strip()
    if not raw_query:
        return []

    query, field = _strip_field_prefix(raw_query)
    if not query:
        return []

    plan = _build_query_plan(query)

    teacher_lookup = _build_teacher_lookup(teachers)
    normalized_query = query.replace(" ", "")
    if field in (None, "name") and normalized_query in teacher_lookup:
        results: List[SearchResult] = []
        for teacher in teacher_lookup[normalized_query]:
            doc = next((d for d in docs if teacher.name in d.path), None)
            if not doc:
                doc = DocRecord(doc_id=teacher.name, path="", text=teacher.personal_intro)
            snippet = _extract_snippet(doc.text, [teacher.name])
            results.append(SearchResult(score=1.0, doc=doc, teacher=teacher, snippet=snippet))
        return _dedupe_and_rank(results, top_k)

    if field in ("papers", "research"):
        scoped = _field_search(plan, field, docs, teachers, top_k)
        if scoped:
            return scoped

    if allow_relax:
        exact_results: List[SearchResult] = []
        for phrase in plan.phrases:
            exact_results.extend(_phrase_search(phrase, docs, teachers, top_k))
        exact_results = _dedupe_and_rank(exact_results, top_k)
        if exact_results:
            return exact_results

        relaxed_results = _token_search(
            plan.tokens,
            docs,
            teachers,
            inverted,
            doc_norms,
            top_k,
            require_all=False,
        )
        if relaxed_results:
            return relaxed_results

    base_results = _token_search(
        plan.tokens,
        docs,
        teachers,
        inverted,
        doc_norms,
        top_k,
        require_all=False,
    )
    if base_results:
        return base_results

    if enable_fuzzy and allow_relax:
        fuzzy_results: List[SearchResult] = []
        for phrase in plan.phrases:
            fuzzy_results.extend(
                _fuzzy_search(phrase, docs, teachers, top_k, threshold=fuzzy_threshold)
            )
        fuzzy_results = _dedupe_and_rank(fuzzy_results, top_k)
        if fuzzy_results:
            return fuzzy_results

    return []


def _format_result(result: SearchResult, rank: int, query: str = "") -> str:
    view = build_display(result, rank, query)
    career = f"  |  {view.career}" if view.career else ""
    lines = [f"[{rank}] {view.name}  |  {view.department}{career}"]
    if view.research:
        lines.append(f"研究方向: {view.research}")
    if view.intro:
        lines.append(f"简介: {view.intro}")
    if view.paper_items:
        lines.append("论文/成果:")
        for i, paper in enumerate(view.paper_items[:8], start=1):
            badge = f"[CCF-{paper.ccf_rank}] " if paper.ccf_rank else ""
            meta = " · ".join(x for x in [paper.venue, paper.year] if x)
            suffix = f" ({meta})" if meta else ""
            lines.append(f"  {i}. {badge}{paper.title}{suffix}")
    elif view.papers:
        lines.append(f"论文/成果: {view.papers}")
    if view.snippet:
        lines.append(f"片段: {view.snippet}")
    if view.keywords:
        lines.append(f"命中关键词: {' / '.join(view.keywords)}")
    if view.url:
        lines.append(f"主页: {view.url}")
    return "\n".join(lines)


def run_cli() -> None:
    teachers = load_teachers(TEACHERS_JSON)
    docs = load_corpus(CORPUS_DIR)
    inverted, doc_norms = build_index(docs)

    print("苏州大学导师检索系统 (基础版)")
    print("输入示例: 自然语言处理 | NLP | ML | events extraction | 周国栋")
    print("输入 quit 退出\n")

    while True:
        query = input("查询> ").strip()
        if not query:
            continue
        if query.lower() in {"quit", "exit"}:
            break

        results = search(query, docs, teachers, inverted, doc_norms)
        if not results:
            print("未找到结果。\n")
            continue

        for i, result in enumerate(results, start=1):
            print(_format_result(result, i, query))
            print("-" * 60)
        print()


if __name__ == "__main__":
    run_cli()
