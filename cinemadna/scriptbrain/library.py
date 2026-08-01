"""ScriptBrain — 题材模板库 (Phase 2)

Phase 2 **不接 LLM**。剧本内容来自本模块的题材模板 + 确定性挑选，
目的不是写出好剧本，而是稳定产出**结构合法、下游能直接消费**的拍摄版 JSON。

后续接真实编剧模型时，只需替换 agents.py 里各 Agent 的实现，
本库可退化为"参考素材/兜底模板"，shooting_script 的结构契约不变。

设计约束：
- 所有 id（location_key / role_key / prop_key）必须是 ASCII —— 它们会变成
  character_id / prop_id，进而变成 Bundle 内的目录名。
- 每个 location 自带 required_atoms，直接对应 SceneDNA 的原子需求。
- difficulty 标 hard 的场景会被 Script Critic 以"当前生成能力做不了"打回。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final


@dataclass(frozen=True)
class LocationSpec:
    key: str
    name: str
    time_of_day: str
    mood: str
    required_atoms: tuple[str, ...]
    camera_intent: str
    spatial_needs: str = "可支持人物走动与特写"
    difficulty: str = "normal"  # normal | hard


@dataclass(frozen=True)
class RoleSpec:
    key: str                  # ASCII，用于 character_id
    name: str
    gender: str
    age_range: str
    role_type: str
    personality_keywords: tuple[str, ...]
    need_age_line: bool = False
    need_family: bool = False


@dataclass(frozen=True)
class PropSpec:
    key: str                  # ASCII，用于 prop_id
    name: str
    description: str
    states: tuple[str, ...]
    story_function: str
    continuity_critical: bool = True


@dataclass(frozen=True)
class GenreTemplate:
    key: str
    label: str
    keywords: tuple[str, ...]
    locations: tuple[LocationSpec, ...]
    roles: tuple[RoleSpec, ...]
    props: tuple[PropSpec, ...]
    hooks: tuple[str, ...]
    conflicts: tuple[str, ...]
    developments: tuple[str, ...]
    cliffhangers: tuple[str, ...]
    dialogue_bank: tuple[str, ...] = field(default=())


# ---------------------------------------------------------------------------
# 都市逆袭
# ---------------------------------------------------------------------------

URBAN_REVENGE = GenreTemplate(
    key="urban_revenge",
    label="都市逆袭",
    keywords=("逆袭", "翻身", "打脸", "职场", "豪门", "赘婿", "落魄", "报复"),
    locations=(
        LocationSpec(
            "rented_room", "县城老旧出租屋", "深夜", "压抑、疲惫",
            ("室内布局", "光线", "家具陈设", "窗户"), "中近景，压迫感",
        ),
        LocationSpec(
            "office_lobby", "写字楼大堂", "清晨", "冷漠、疏离",
            ("挑高空间", "冷白光线", "前台", "玻璃幕墙"), "广角，人物渺小",
        ),
        LocationSpec(
            "meeting_room", "公司会议室", "白天", "紧绷、对峙",
            ("长桌", "百叶窗光", "投影幕", "座位秩序"), "过肩双人，对峙构图",
        ),
        LocationSpec(
            "night_street", "城中村街口", "夜晚", "躁动、危险",
            ("街道纵深", "招牌霓虹", "路面积水", "人流"), "手持跟拍",
        ),
    ),
    roles=(
        RoleSpec("linwan", "林晚", "female", "25-28", "主角",
                 ("压抑", "坚韧", "疲惫"), need_age_line=True),
        RoleSpec("zhaosheng", "赵胜", "male", "40-45", "对手",
                 ("倨傲", "势利", "掌控欲")),
        RoleSpec("chenmu", "陈姆", "female", "50-55", "配角",
                 ("刻薄", "现实")),
    ),
    props=(
        PropSpec("hospital_bill", "医院缴费单", "旧的纸质医院缴费单",
                 ("完整", "被攥皱", "放在桌上"), "建立女主经济压力"),
        PropSpec("work_badge", "工牌", "写字楼门禁工牌",
                 ("崭新", "被摔裂"), "身份与羞辱的载体"),
    ),
    hooks=("催债电话在凌晨三点响起", "她被当众收回工牌", "一张缴费单压垮了最后一根弦"),
    conflicts=("当众被羞辱，她第一次没有低头", "对手抛出一份必须签的协议",
               "旧账被翻出来，退路被彻底堵死"),
    developments=("她开始悄悄收集证据", "一次不起眼的选择埋下反转伏笔"),
    cliffhangers=("她把那份协议撕了", "监控画面里出现了不该出现的人",
                  "电话那头说：他还活着"),
    dialogue_bank=("我没有退路了。", "你以为我还怕什么？", "这次换我来定规矩。",
                   "钱我会还，但不是这么还。"),
)

# ---------------------------------------------------------------------------
# 悬疑追凶
# ---------------------------------------------------------------------------

SUSPENSE = GenreTemplate(
    key="suspense",
    label="悬疑追凶",
    keywords=("悬疑", "凶手", "失踪", "真相", "谜", "命案", "追凶", "调查"),
    locations=(
        LocationSpec(
            "old_corridor", "老式居民楼走廊", "深夜", "阴冷、不安",
            ("走廊纵深", "声控灯", "斑驳墙面", "铁门"), "低机位跟随",
        ),
        LocationSpec(
            "police_room", "派出所询问室", "白天", "压迫、冷静",
            ("单向玻璃", "顶灯", "金属桌椅", "记录本"), "固定机位对切",
        ),
        LocationSpec(
            "riverside", "河边堤岸", "黄昏", "空旷、孤绝",
            ("河岸线", "逆光", "栏杆", "远处桥影"), "远景推近",
        ),
        LocationSpec(
            "underwater_search", "水下打捞现场", "夜晚", "窒息、危险",
            ("水下浑浊", "探照灯束", "浮标"), "水下摄影",
            difficulty="hard",
        ),
    ),
    roles=(
        RoleSpec("shenli", "沈砺", "male", "35-40", "主角",
                 ("克制", "偏执", "敏锐"), need_age_line=True),
        RoleSpec("wenqing", "闻晴", "female", "28-32", "对手",
                 ("冷静", "深藏不露")),
        RoleSpec("laoyu", "老于", "male", "55-60", "配角",
                 ("圆滑", "疲惫")),
    ),
    props=(
        PropSpec("evidence_bag", "物证袋", "封装着关键物证的透明证物袋",
                 ("密封", "被拆开", "空的"), "推动真相翻转"),
        PropSpec("old_photo", "旧照片", "边角磨损的合影",
                 ("完整", "被撕去一角"), "揭示人物关系"),
    ),
    hooks=("走廊尽头的灯忽然灭了", "失踪三年的人出现在监控里", "物证袋是空的"),
    conflicts=("两条线索互相矛盾，必须放弃一条", "证人当场翻供",
               "上级要求今晚就结案"),
    developments=("他重新走了一遍案发路线", "一句无心的话对不上时间线"),
    cliffhangers=("照片背面写着他自己的名字", "门在身后被反锁",
                  "她说：你查的人，是我。"),
    dialogue_bank=("时间对不上。", "你在替谁圆谎？", "再给我一晚。",
                   "这件事，你从一开始就知道。"),
)

# ---------------------------------------------------------------------------
# 家庭伦理
# ---------------------------------------------------------------------------

FAMILY = GenreTemplate(
    key="family",
    label="家庭伦理",
    keywords=("家庭", "婆媳", "母亲", "女儿", "伦理", "亲情", "离婚", "养老"),
    locations=(
        LocationSpec(
            "family_kitchen", "老房子厨房", "傍晚", "琐碎、隐忍",
            ("灶台", "抽油烟机光", "碗碟", "小窗"), "近景特写，手部动作",
        ),
        LocationSpec(
            "living_room", "客厅", "夜晚", "沉默、对峙",
            ("沙发布局", "顶灯", "电视机", "全家福"), "全景压构图",
        ),
        LocationSpec(
            "hospital_corridor", "医院走廊", "清晨", "焦灼、无助",
            ("走廊纵深", "冷白光线", "长椅", "指示牌"), "长焦跟拍",
        ),
    ),
    roles=(
        RoleSpec("suqin", "苏芹", "female", "45-50", "主角",
                 ("隐忍", "要强", "疲惫"), need_age_line=True, need_family=True),
        RoleSpec("zhouhe", "周禾", "female", "22-25", "配角",
                 ("倔强", "疏离")),
        RoleSpec("laozhou", "老周", "male", "50-55", "对手",
                 ("固执", "沉默")),
    ),
    props=(
        PropSpec("medical_report", "体检报告", "折了两折的体检报告单",
                 ("折叠", "摊开", "被塞进抽屉"), "隐瞒与揭穿的核心道具"),
        PropSpec("family_photo", "全家福", "褪色的相框合影",
                 ("挂在墙上", "被摘下"), "关系断裂的象征"),
    ),
    hooks=("她把报告塞进了抽屉最深处", "女儿三年来第一次进门",
           "一顿饭没人说话"),
    conflicts=("钱要给谁治，必须当场定", "旧事被当着所有人揭开",
               "她第一次对母亲喊出声"),
    developments=("她一个人去补交了费用", "两个人在厨房各自沉默"),
    cliffhangers=("抽屉被拉开了", "她收拾了行李站在门口", "电话里说：还有一次机会"),
    dialogue_bank=("我没事。", "这个家还要不要了？", "你从来没问过我想要什么。",
                   "再撑一撑，就一撑。"),
)

# ---------------------------------------------------------------------------
# 都市写实（兜底题材）
# ---------------------------------------------------------------------------

URBAN_REALISM = GenreTemplate(
    key="urban_realism",
    label="都市写实",
    keywords=(),
    locations=(
        LocationSpec(
            "small_apartment", "城市单身公寓", "夜晚", "疲惫、孤独",
            ("室内布局", "台灯光", "简易家具", "窗外楼景"), "中景，静态构图",
        ),
        LocationSpec(
            "convenience_store", "便利店", "凌晨", "冷清、清醒",
            ("货架排列", "冷白灯管", "收银台", "玻璃门"), "定机位长镜",
        ),
        LocationSpec(
            "subway_platform", "地铁站台", "早高峰", "拥挤、麻木",
            ("站台纵深", "顶灯", "人流", "指示牌"), "手持跟拍",
        ),
        LocationSpec(
            "rooftop", "老楼天台", "黄昏", "开阔、犹疑",
            ("天台围栏", "夕照", "城市远景", "水塔"), "广角，人物置于边角",
        ),
    ),
    roles=(
        RoleSpec("jiangnan", "江楠", "female", "26-30", "主角",
                 ("清醒", "倔强", "疲惫"), need_age_line=True),
        RoleSpec("xuke", "徐可", "male", "30-35", "对手",
                 ("现实", "圆滑")),
        RoleSpec("xiaomeng", "小蒙", "female", "20-23", "配角",
                 ("天真", "热心")),
    ),
    props=(
        PropSpec("resignation_letter", "辞职信", "手写的一页辞职信",
                 ("完整", "被揉皱", "摊在桌上"), "决断的实体化"),
        PropSpec("phone_notice", "手机欠费通知", "屏幕上的欠费短信",
                 ("未读", "已读"), "生活压力的具象", continuity_critical=False),
    ),
    hooks=("她在凌晨的便利店把信写完了", "手机在最不该响的时候响了",
           "她第一次没有挤上那班地铁"),
    conflicts=("必须在今天之内给出答复", "对方把话说死了",
               "她发现自己根本没有选择"),
    developments=("她绕了很远的路回家", "一件小事让她改了主意"),
    cliffhangers=("她按下了发送", "门外站着不该来的人", "她把信撕了"),
    dialogue_bank=("我想清楚了。", "你不用替我做决定。", "还能怎么办呢。",
                   "这次我自己来。"),
)


ALL_GENRES: Final[tuple[GenreTemplate, ...]] = (
    URBAN_REVENGE,
    SUSPENSE,
    FAMILY,
    URBAN_REALISM,
)

#: 兜底题材：关键词一个都没命中时使用
FALLBACK_GENRE: Final[GenreTemplate] = URBAN_REALISM

#: 三段式节拍（每集必须齐全：开头钩子 + 中段冲突 + 结尾悬念）
BEAT_HOOK: Final = "HOOK"
BEAT_CONFLICT: Final = "CONFLICT"
BEAT_DEVELOPMENT: Final = "DEVELOPMENT"
BEAT_CLIFFHANGER: Final = "CLIFFHANGER"

REQUIRED_BEATS: Final[frozenset[str]] = frozenset(
    {BEAT_HOOK, BEAT_CONFLICT, BEAT_CLIFFHANGER}
)


def get_genre(key: str) -> GenreTemplate:
    """按 key 取题材模板，不存在则抛 KeyError。"""
    for g in ALL_GENRES:
        if g.key == key:
            return g
    raise KeyError(f"未知题材: {key!r}（可选: {[g.key for g in ALL_GENRES]}）")


def match_genre(theme: str) -> GenreTemplate:
    """按关键词命中数归类题材；一个都没命中则用兜底题材。

    命中数相同时按 ALL_GENRES 的顺序取先者，保证结果稳定。
    """
    best: GenreTemplate | None = None
    best_hits = 0
    for g in ALL_GENRES:
        hits = sum(1 for kw in g.keywords if kw in theme)
        if hits > best_hits:
            best, best_hits = g, hits
    return best or FALLBACK_GENRE
