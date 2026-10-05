"""素材分档加工配置：门控参数 + 类型目录 + 类型提示词，存 `system_setting` KV。

红线（同 `comment_settings.py`）：门控阈值/类型目录/类型提示词**不写死在业务代码里**，
有内置默认、可在线改、读取时取最新值。写入侧校验放 API 层（Task 8）。
"""

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SystemSetting

# 类型标签（用字符串而非 type，避免 bool 被当作 int 子类误判）
_INT = "int"
_FLOAT = "float"
_BOOL = "bool"
_STR = "str"

# 对外短名 -> system_setting key（单一事实来源；API / 服务复用此映射）
PROCESS_KEYS: dict[str, str] = {
    "l2_min_heat": "process_l2_min_heat",
    "l3_min_heat": "process_l3_min_heat",
    "min_likes_floor": "process_min_likes_floor",
    "budget": "process_budget",
    "top_pct": "process_top_pct",
    "weight_like": "process_weight_like",
    "weight_comment": "process_weight_comment",
    "weight_play": "process_weight_play",
    "frames_per_min": "process_frames_per_min",
    "max_frames": "process_max_frames",
    "video_max_mb": "process_video_max_mb",
    "classify_enabled": "process_classify_enabled",
    "use_cover": "process_use_cover",
    "cover_max_mb": "process_cover_max_mb",
    "low_confidence_threshold": "process_low_confidence_threshold",
    "provider": "process_provider",
    "vision_model": "process_vision_model",
    "classify_model": "process_classify_model",
}

# system_setting key -> (默认值, 类型)。默认值可被 system_setting 覆盖。
_DEFAULTS: dict[str, tuple[object, str]] = {
    "process_l2_min_heat": (200, _INT),
    "process_l3_min_heat": (1000, _INT),
    "process_min_likes_floor": (100, _INT),
    "process_budget": (20, _INT),
    "process_top_pct": (0.2, _FLOAT),
    "process_weight_like": (1.0, _FLOAT),
    "process_weight_comment": (2.0, _FLOAT),
    "process_weight_play": (0.0, _FLOAT),
    "process_frames_per_min": (6.0, _FLOAT),
    "process_max_frames": (24, _INT),
    "process_video_max_mb": (15.0, _FLOAT),
    "process_classify_enabled": (True, _BOOL),
    "process_use_cover": (False, _BOOL),
    "process_cover_max_mb": (5.0, _FLOAT),
    # 判类置信度低于此值 → 标 low_confidence 供人工复核（设计 §4 默认 0.6）。
    "process_low_confidence_threshold": (0.6, _FLOAT),
    "process_provider": ("qwen", _STR),
    "process_vision_model": ("qwen3.8-flash", _STR),
    "process_classify_model": ("qwen3.8-flash", _STR),
}

# 类型目录（枚举 -> 中文），设计 §4 的 16 类 + other。
DEFAULT_TAXONOMY: dict[str, str] = {
    "knowledge": "知识科普",
    "opinion": "观点评论/热点解读",
    "commerce": "带货/种草/开箱",
    "drama": "剧情/段子/情景",
    "tutorial": "教程/技能教学",
    "review": "测评/探店",
    "emotion": "情感/鸡汤/励志",
    "interview": "访谈/对话",
    "other": "其他/无法归类",
    "news": "新闻资讯/纪实",
    "vlog": "日常 vlog/生活记录",
    "performance": "才艺/舞蹈/音乐",
    "game": "游戏",
    "film_tv": "影视解说/剪辑混剪",
    "food": "美食制作/吃播",
    "pet": "萌宠",
    "sports": "体育赛事",
}

# 通用维度（多数类型共有）。设计 §5。
COMMON_DIMENSIONS: list[dict[str, str]] = [
    {"key": "visual", "name": "画面内容", "ask": "主体、场景、动作、画面风格"},
    {"key": "speech", "name": "语音/文案", "ask": "口播要点、字幕、旁白（可与转录互补）"},
    {"key": "structure", "name": "内容结构", "ask": "开头钩子→展开→转折→结尾的段落划分"},
    {"key": "emotion", "name": "情绪与节奏", "ask": "情绪基调、节奏快慢、情绪曲线"},
    {"key": "highlights", "name": "可复用点", "ask": "金句、名场面、可二创的画面"},
    {"key": "scene_count", "name": "场景数", "ask": "大致的场景/镜头切换数"},
]


def _d(key: str, name: str, ask: str) -> dict[str, str]:
    return {"key": key, "name": name, "ask": ask}


# 类型专属提示词（Focus + 追加维度）。设计 §5；`dimensions` 是**该类型追加**的维度，
# 服务层使用时与 COMMON_DIMENSIONS 合并。设计未给出追加维度的类型（opinion/other）留空，
# 仅靠通用维度 + Focus 区分。
DEFAULT_TYPE_PROMPTS: dict[str, dict] = {
    "knowledge": {
        "focus": "重点提炼核心论点与论据链、关键数据与可复述的结论，区分事实与推测。",
        "dimensions": [
            _d("thesis", "核心论点/目标", "这条视频要证明什么、要讲清什么"),
            _d("steps", "步骤/论据", "支撑论点的步骤、论据、例子"),
            _d("takeaway", "结论/要点", "看完应该记住的结论或要点"),
        ],
    },
    "opinion": {
        "focus": "提炼核心观点、论据与立场，区分事实陈述与个人看法，抓热点指向。",
        "dimensions": [],
    },
    "commerce": {
        "focus": "重点捕捉商品外观与卖点话术、价格与优惠、下单引导、限时/稀缺感与信任背书。",
        "dimensions": [
            _d("product", "商品与卖点", "商品/品牌/外观、被强调的核心卖点"),
            _d("price_offer", "价格/优惠/规格", "报价、优惠、规格与到手价"),
            _d("cta", "转化号召", "下单引导话术（扣1/点链接/领券等）"),
            _d("trust", "信任背书", "销量、口碑、资质、限时稀缺等背书"),
        ],
    },
    "drama": {
        "focus": "梳理人物关系与冲突，抓反转/笑点与情绪爆点，注意台词梗。",
        "dimensions": [
            _d("characters", "人物关系", "出场人物及其关系"),
            _d("conflict", "冲突", "矛盾/冲突点与铺垫"),
            _d("twist", "反转/笑点", "反转、笑点或出人意料的结尾"),
        ],
    },
    "tutorial": {
        "focus": "提炼可复现的目标与步骤、关键参数与注意事项，突出可操作结论。",
        "dimensions": [
            _d("thesis", "核心目标", "教会观众做什么"),
            _d("steps", "步骤", "按顺序的操作步骤与关键参数"),
            _d("takeaway", "结论/要点", "易错点、注意事项与最终效果"),
        ],
    },
    "review": {
        "focus": "明确测评对象，逐项列优缺点与实测结论，给出是否推荐。",
        "dimensions": [
            _d("target", "测评对象", "具体产品/店铺/型号"),
            _d("pros_cons", "优缺点", "优点与缺点/不足"),
            _d("verdict", "结论/是否推荐", "总体结论与推荐人群"),
        ],
    },
    "emotion": {
        "focus": "抓主题情绪与情绪转折，提炼可共鸣的金句与叙事线。",
        "dimensions": [
            _d("theme", "主题情绪", "核心情绪主题（治愈/励志/遗憾等）"),
            _d("turning_point", "情绪转折", "情绪由低到高或反转的节点"),
            _d("quotes", "共鸣金句", "可直接引用的共鸣语句"),
        ],
    },
    "interview": {
        "focus": "识别嘉宾与身份，梳理话题线，抓代表性问答与金句。",
        "dimensions": [
            _d("guest", "嘉宾", "嘉宾身份/背景"),
            _d("topics", "话题", "讨论到的主要话题"),
            _d("notable_qa", "代表问答", "有信息量或冲突感的问答"),
        ],
    },
    "other": {
        "focus": "按通用维度尽量客观描述视频内容，不确定处留空，不要编造。",
        "dimensions": [],
    },
    "news": {
        "focus": "抓事件要素（5W）与时间线，标注信息来源与报道立场。",
        "dimensions": [
            _d("event", "事件要素", "人物/时间/地点/起因/经过（5W）"),
            _d("timeline", "时间线", "事件关键节点顺序"),
            _d("stance", "立场/来源", "信源与报道立场倾向"),
        ],
    },
    "vlog": {
        "focus": "记录地点与活动线，描摹生活氛围与情绪。",
        "dimensions": [
            _d("location", "地点", "出现的地点/场景"),
            _d("activity", "活动线", "一路发生的事情与安排"),
            _d("mood", "生活氛围", "整体生活氛围与情绪"),
        ],
    },
    "performance": {
        "focus": "识别才艺类型，抓卡点与高光瞬间，注意节奏与难度（highlights 见通用维度）。",
        "dimensions": [
            _d("act_type", "才艺类型", "唱/跳/乐器/杂技等"),
            _d("tempo", "节奏/卡点", "节奏快慢与卡点位置"),
        ],
    },
    "game": {
        "focus": "识别游戏与场景，抓高光/关键操作，记录解说风格。",
        "dimensions": [
            _d("game_title", "游戏", "游戏名称与模式"),
            _d("moment", "高光操作/剧情", "关键操作或剧情节点"),
            _d("commentary", "解说风格", "解说语气与口头禅"),
        ],
    },
    "film_tv": {
        "focus": "理清原片/素材来源与剧情，概述剪辑风格与解说话术。",
        "dimensions": [
            _d("source", "原片/素材来源", "影视作品名与素材来源"),
            _d("plot_summary", "剧情概述", "剧情脉络概述"),
            _d("editing_style", "剪辑风格/解说话术", "剪辑节奏与解说风格"),
        ],
    },
    "food": {
        "focus": "识别菜品与食材，还原制作过程，描述口感与卖相。",
        "dimensions": [
            _d("dish", "菜品", "菜名与成品呈现"),
            _d("ingredients", "食材", "用到的主要食材/调料"),
            _d("process", "制作过程/口感", "关键步骤与口感评价"),
        ],
    },
    "pet": {
        "focus": "识别宠物与其行为，抓萌点与名场面。",
        "dimensions": [
            _d("animals", "宠物", "宠物种类与特征"),
            _d("cute_moments", "萌点/名场面", "可爱瞬间与互动"),
        ],
    },
    "sports": {
        "focus": "识别赛事与对阵，抓关键瞬间与结果。",
        "dimensions": [
            _d("match", "赛事/对阵", "赛事名称与对阵双方"),
            _d("key_moment", "关键瞬间", "决定性回合/进球/失误"),
            _d("result", "结果", "比分与最终结果"),
        ],
    },
}

_COMMON_KEY = "process_prompt_common"
_TYPE_PROMPTS_KEY = "process_type_prompts"
_TAXONOMY_KEY = "process_type_taxonomy"


def _coerce(raw: str | None, default: object, kind: str) -> object:
    """把 KV 里的字符串按声明类型归一；空/非法一律回退默认。"""
    if raw is None or raw == "":
        return default
    try:
        if kind == _INT:
            return int(str(raw).strip())
        if kind == _FLOAT:
            return float(str(raw).strip())
        if kind == _BOOL:
            text = str(raw).strip().lower()
            if text in ("1", "true", "yes", "on"):
                return True
            if text in ("0", "false", "no", "off"):
                return False
            return default
        return str(raw)
    except (TypeError, ValueError):
        return default


def _rows(db: Session) -> dict[str, str]:
    return {s.key: s.value for s in db.scalars(select(SystemSetting)).all()}


def get_process_settings(db: Session) -> dict:
    """返回门控/加工数值参数（短名 -> 值），默认兜底、类型归一。"""
    rows = _rows(db)
    out: dict = {}
    for short, key in PROCESS_KEYS.items():
        default, kind = _DEFAULTS[key]
        out[short] = _coerce(rows.get(key), default, kind)
    return out


def _load_json(raw: str | None, validator):
    """解析 KV 里的 JSON；解析失败或结构非法一律回退默认。"""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if validator(data) else None


def _valid_taxonomy(data: object) -> bool:
    # 必须非空：空目录会让判类把所有类型归一为 other，静默退化类型化提示词。
    return (
        isinstance(data, dict)
        and len(data) >= 1
        and all(isinstance(k, str) and isinstance(v, str) for k, v in data.items())
    )


def _valid_dimension(dim: object) -> bool:
    return (
        isinstance(dim, dict)
        and isinstance(dim.get("key"), str)
        and dim["key"].strip() != ""
        and isinstance(dim.get("name"), str)
        and isinstance(dim.get("ask"), str)
    )


def _valid_type_prompts(data: object) -> bool:
    # 必须非空：空提示词会让理解失去类型专属 Focus/维度。
    if not isinstance(data, dict) or len(data) == 0:
        return False
    for spec in data.values():
        if not isinstance(spec, dict):
            return False
        if not isinstance(spec.get("focus"), str):
            return False
        dims = spec.get("dimensions")
        if not isinstance(dims, list) or not all(_valid_dimension(d) for d in dims):
            return False
    return True


def _valid_dimension_list(data: object) -> bool:
    # 必须非空：空通用维度会让维度提取失去兜底清单。
    return isinstance(data, list) and len(data) >= 1 and all(_valid_dimension(d) for d in data)


def get_taxonomy(db: Session) -> dict[str, str]:
    """返回类型枚举 -> 中文；损坏时回退内置默认。"""
    data = _load_json(_rows(db).get(_TAXONOMY_KEY), _valid_taxonomy)
    return data if data is not None else DEFAULT_TAXONOMY


def get_type_prompts(db: Session) -> dict[str, dict]:
    """返回类型 -> {"focus", "dimensions"}；损坏时回退内置默认。

    注意：`dimensions` 为**该类型追加**的维度，服务层需与 `COMMON_DIMENSIONS` 合并使用。
    """
    data = _load_json(_rows(db).get(_TYPE_PROMPTS_KEY), _valid_type_prompts)
    return data if data is not None else DEFAULT_TYPE_PROMPTS


def get_common_dimensions(db: Session) -> list[dict[str, str]]:
    """返回通用维度清单；损坏时回退内置默认。"""
    data = _load_json(_rows(db).get(_COMMON_KEY), _valid_dimension_list)
    return data if data is not None else COMMON_DIMENSIONS
