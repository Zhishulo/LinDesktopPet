# -*- coding: utf-8 -*-
"""心理评测服务（100% 本地）：16 套标准量表 + 自动计分 + 本地存储。

题库整理自 ClouderyStudio/psychology（心灵驿站）开源项目与公开量表，
仅用于自我了解，不构成医学诊断。所有作答数据保存在程序旁的
assessment.json，绝不上传。
"""
import time

from data import load_json, save_json
from scales_data import SCALES as SCALES_DATA

ASSESSMENT_FILE = "assessment.json"

# 常用内置量表（保证最基础的两个入口始终可用，与题库文件无关）
_BUILTIN = [
    {
        "key": "phq9",
        "name": "PHQ-9 抑郁自评",
        "category": "症状筛查",
        "desc": "过去两周内，你多久会受到以下问题的困扰？",
        "options": ["完全不会", "有几天", "一半以上天数", "几乎每天"],
        "questions": [
            "做事时提不起劲或没有兴趣",
            "感到心情低落、沮丧或绝望",
            "入睡困难、睡不安稳或睡眠过多",
            "感觉疲倦或没有活力",
            "食欲不振或吃太多",
            "觉得自己很糟，或觉得自己让家人失望",
            "做事时无法专心（如阅读或看电视）",
            "行动或说话变得缓慢，或烦躁不安、坐立难安",
            "有不如死掉或用某种方式伤害自己的念头",
        ],
        "crisis_question": 9,
        "crisis_text": "你在「有伤害自己的想法」一题上勾选了非「完全不会」。",
        "scoring": {
            "type": "sum",
            "max": 27,
            "levels": [
                {"min": 0, "max": 4, "level": "无明显抑郁症状",
                 "suggestion": "状态不错，继续保持～"},
                {"min": 5, "max": 9, "level": "轻度抑郁",
                 "suggestion": "有一些低落情绪，值得好好照顾自己。"},
                {"min": 10, "max": 14, "level": "中度抑郁",
                 "suggestion": "情绪负担比较重，建议和信任的人聊聊，或寻求专业支持。"},
                {"min": 15, "max": 19, "level": "中重度抑郁",
                 "suggestion": "你辛苦了，请一定考虑寻求专业帮助，你值得被温柔对待。"},
                {"min": 20, "max": 27, "level": "重度抑郁",
                 "suggestion": "请务必尽快寻求专业帮助，麟会一直陪着你。"},
            ],
        },
    },
    {
        "key": "gad7",
        "name": "GAD-7 焦虑自评",
        "category": "症状筛查",
        "desc": "过去两周内，你多久会受到以下问题的困扰？",
        "options": ["完全不会", "有几天", "一半以上天数", "几乎每天"],
        "questions": [
            "感到紧张、焦虑或急切",
            "无法停止或控制担忧",
            "对各种各样的事情担忧过多",
            "很难放松下来",
            "坐立不安，以至于难以安静地坐着",
            "变得容易烦恼或易怒",
            "感到好像有什么可怕的事情会发生",
        ],
        "crisis_question": -1,
        "scoring": {
            "type": "sum",
            "max": 21,
            "levels": [
                {"min": 0, "max": 4, "level": "无明显焦虑症状",
                 "suggestion": "状态不错，继续保持～"},
                {"min": 5, "max": 9, "level": "轻度焦虑",
                 "suggestion": "有一些紧张感，记得多休息、深呼吸。"},
                {"min": 10, "max": 14, "level": "中度焦虑",
                 "suggestion": "焦虑感比较明显，建议找信任的人聊聊，或寻求专业支持。"},
                {"min": 15, "max": 21, "level": "重度焦虑",
                 "suggestion": "你辛苦了，请务必考虑尽快寻求专业帮助。"},
            ],
        },
    },
]


def _normalize(scale):
    """把题库 dict 规范成通用结构：options 统一为 {value,label}，levels 统一为 dict"""
    scale = dict(scale)
    qs = scale.get("questions", [])
    if qs and isinstance(qs[0], str):
        scale["questions"] = [{"id": i + 1, "text": s} for i, s in enumerate(qs)]
    opts = scale.get("options", [])
    if isinstance(opts, dict):
        pass  # 16PF：各选项集已是 {value,label} 列表
    elif opts and isinstance(opts[0], str):
        scale["options"] = [{"value": i, "label": s} for i, s in enumerate(opts)]
    lv = scale.get("levels") or scale.get("scoring", {}).get("levels") or []
    norm_lv = []
    for item in lv:
        if isinstance(item, tuple):
            lo, hi, name, desc = item
            norm_lv.append({"min": lo, "max": hi, "level": name,
                            "suggestion": desc})
        else:
            d = dict(item)
            d.setdefault("suggestion", d.get("description", ""))
            norm_lv.append(d)
    scoring = dict(scale.get("scoring", {}) or {})
    scoring.setdefault("type", "sum")
    if norm_lv:
        scoring["levels"] = norm_lv
    scale["scoring"] = scoring
    return scale


SCALES = [_normalize(s) for s in _BUILTIN] + [_normalize(s) for s in SCALES_DATA.values()]
SCALES_BY_KEY = {s["key"]: s for s in SCALES}

_CATEGORY_ORDER = ["症状筛查", "人格性格", "专项量表"]


def scales_by_category():
    """按分类分组返回量表列表，供选择页使用"""
    groups = {}
    for s in SCALES:
        groups.setdefault(s.get("category", "其他"), []).append(s)
    ordered = []
    seen = set()
    for cat in _CATEGORY_ORDER:
        if cat in groups:
            ordered.append((cat, groups[cat]))
            seen.add(cat)
    for cat, items in groups.items():
        if cat not in seen:
            ordered.append((cat, items))
    return ordered


def default_assessments():
    return {"records": []}


def load_assessments():
    """读取全部评测记录；旧档缺字段自动补默认值"""
    data = load_json(ASSESSMENT_FILE, None)
    if data is None:
        data = default_assessments()
        save_assessments(data)
    data.setdefault("records", [])
    return data


def save_assessments(data):
    save_json(ASSESSMENT_FILE, data)


def _opt_values(scale, q):
    """返回某题选项分值列表（16PF 每题可按 option_type 取不同选项集）"""
    opts = scale["options"]
    if isinstance(opts, dict):
        key = q.get("option_type") or "normal"
        opts = opts.get(key, [])
    return [o["value"] for o in opts]


def _opt_labels(scale, q):
    opts = scale["options"]
    if isinstance(opts, dict):
        key = q.get("option_type") or "normal"
        opts = opts.get(key, [])
    return [o["label"] for o in opts]


def _rev_value(q, val, vals):
    """反向题：分值按选项取值区间翻转"""
    if q.get("reverse"):
        lo, hi = min(vals), max(vals)
        return lo + hi - val
    return val


def _match_level(levels, score):
    for lv in levels:
        lo = lv.get("min", 0)
        hi = lv.get("max", 1 << 30)
        if lo <= score <= hi:
            return lv.get("level", ""), lv.get("suggestion", ""), lv
    return "", "", {}


def _round(x, nd=2):
    return round(x + 1e-9, nd)


def _score_sum(scale, values, detail):
    total = sum(values)
    levels = scale["scoring"].get("levels", [])
    level, advice, _ = _match_level(levels, total)
    maxv = scale["scoring"].get("max") or sum(max(_opt_values(scale, q)) for q in scale["questions"])
    return {"total": total, "max": maxv, "level": level, "advice": advice,
            "severity": (total / maxv) if maxv else 0}


def _score_zung(scale, values, detail):
    """SDS / SAS：粗分 → 标准分（×1.25）"""
    raw = sum(values)
    std = round(raw * 1.25)
    level, advice, _ = _match_level(scale["scoring"].get("levels", []), std)
    return {"total": std, "max": 100, "level": level, "advice": advice,
            "severity": std / 100.0, "raw": raw, "standardized": std,
            "dimensions": {"标准分": {"value": std, "raw": raw,
                                     "desc": "标准分 = 粗分 × 1.25，{}".format(level)}}}


def _score_scl90(scale, values, detail):
    total = sum(values)
    avg = total / max(len(values), 1)
    dims = {}
    for key, info in scale.get("dimension_items", {}).items():
        items = info.get("items", [])
        vals = [values[i - 1] for i in items if 1 <= i <= len(values)]
        if not vals:
            continue
        d_avg = sum(vals) / len(vals)
        d_level, d_desc, _ = _match_level(scale.get("dimension_levels", []), d_avg)
        dims[key] = {"name": info.get("name", key), "total": sum(vals),
                     "avg": _round(d_avg), "level": d_level, "desc": d_desc}
    level, advice, _ = _match_level(scale["scoring"].get("levels", []), avg)
    top = sorted(dims.values(), key=lambda d: -d["avg"])[:3]
    top_desc = "、".join(d["name"] for d in top) if top else ""
    return {"total": _round(avg), "max": 5, "level": level, "advice": advice,
            "severity": avg / 5.0, "raw": total, "standardized": _round(avg),
            "dimensions": dims, "top_dimensions": top_desc,
            "total_label": "总均分"}


def _score_sccs(scale, values, detail):
    dims = {"disharmony": {"total": 0, "count": 0},
            "flexibility": {"total": 0, "count": 0},
            "rigidity": {"total": 0, "count": 0}}
    for q, val in zip(scale["questions"], values):
        d = q.get("dimension")
        if d in dims:
            dims[d]["total"] += val
            dims[d]["count"] += 1
    dh = dims["disharmony"]["total"] / max(dims["disharmony"]["count"], 1)
    fl = dims["flexibility"]["total"] / max(dims["flexibility"]["count"], 1)
    rg = dims["rigidity"]["total"] / max(dims["rigidity"]["count"], 1)
    harmony = (6 - dh) * 0.5 + fl * 0.3 + (6 - rg) * 0.2
    level, advice, _ = _match_level([
        {"min": 4.5, "max": 5.0, "level": "高度和谐",
         "suggestion": "内心与外在经验整合良好，面对变化灵活从容。"},
        {"min": 3.5, "max": 4.49, "level": "比较和谐",
         "suggestion": "多数时候能接纳自己、适应环境，整体保持平衡。"},
        {"min": 2.5, "max": 3.49, "level": "一般",
         "suggestion": "可能在某些方面感到内心矛盾，建议以更开放灵活的态度面对自己。"},
        {"min": 1.5, "max": 2.49, "level": "不太和谐",
         "suggestion": "可能经常感到内心冲突，建议探索内心矛盾，学习灵活应对。"},
        {"min": 0, "max": 1.49, "level": "严重不和谐",
         "suggestion": "建议考虑寻求专业心理咨询师的帮助，系统地探索自我。"},
    ], harmony)
    dims_out = {
        "disharmony": {"name": "自我与经验的不和谐", "avg": _round(dh),
                       "desc": "均值越高，内心冲突越多"},
        "flexibility": {"name": "自我的灵活性", "avg": _round(fl),
                        "desc": "均值越高，适应变化能力越强"},
        "rigidity": {"name": "自我的刻板性", "avg": _round(rg),
                     "desc": "均值越高，越习惯固定方式行事"},
    }
    dims_out["harmony"] = {"name": "综合和谐指数", "value": _round(harmony),
                           "desc": level}
    return {"total": round(harmony * 20), "max": 100, "level": level,
            "advice": advice, "severity": 1 - harmony / 5.0,
            "dimensions": dims_out, "total_label": "和谐指数"}


def _score_temperament(scale, values, detail):
    names = {"choleric": "胆汁质", "sanguine": "多血质",
             "phlegmatic": "黏液质", "melancholic": "抑郁质"}
    scores = {k: 0 for k in names}
    for q, val in zip(scale["questions"], values):
        d = q.get("dimension")
        if d in scores:
            scores[d] += val
    ordered = sorted(scores.items(), key=lambda kv: -kv[1])
    primary, p_score = ordered[0]
    secondary, s_score = ordered[1]
    diff = p_score - s_score
    if diff >= 4:
        type_name = names[primary]
    else:
        type_name = "{}-{}混合型".format(names[primary], names[secondary])
    if p_score > 20:
        typical = "典型"
    elif p_score >= 10:
        typical = "一般"
    else:
        typical = "倾向"
    dims = {k: {"name": names[k], "total": v, "avg": _round(v / 15.0)}
            for k, v in scores.items()}
    return {"total": p_score, "max": 30, "level": typical + type_name,
            "advice": "最高分维度为{}（{}分）。气质没有好坏之分，了解它只是为了更好地与自己和他人相处。".format(
                names[primary], p_score),
            "severity": 0.0, "dimensions": dims, "total_label": "主要维度分"}


def _score_bpns(scale, values, detail):
    names = {"autonomy": "自主需求", "competence": "胜任需求",
             "relatedness": "归属需求"}
    dims = {k: {"total": 0, "count": 0} for k in names}
    for q, val in zip(scale["questions"], values):
        d = q.get("dimension")
        if d in dims:
            dims[d]["total"] += val
            dims[d]["count"] += 1
    avg = {}
    level = {}
    for k, v in dims.items():
        a = v["total"] / max(v["count"], 1)
        avg[k] = _round(a)
        level[k] = "高度满足" if a >= 5.5 else "基本满足" if a >= 4 else \
            "部分满足" if a >= 2.5 else "满足不足"
    total_avg = sum(avg.values()) / max(len(avg), 1)
    total_score = round(total_avg / 7.0 * 100)
    overall = "心理需求满足良好" if total_avg >= 5.5 else \
        "心理需求基本满足" if total_avg >= 4 else \
        "心理需求部分满足" if total_avg >= 2.5 else "心理需求满足不足"
    dims_out = {k: {"name": names[k], "avg": avg[k], "level": level[k]}
                for k in names}
    return {"total": total_score, "max": 100, "level": overall,
            "advice": "总分 {} / 100，三个维度的满足程度如上。需求得到满足时，人更有活力和安全感；"
                      "有不足时，可以从环境、关系和自我照顾三方面慢慢调整。".format(total_score),
            "severity": 1 - total_avg / 7.0, "dimensions": dims_out,
            "total_label": "需求满足度"}


def _score_ipip(scale, values, detail):
    dims_info = scale.get("dimensions", {})
    dims = {}
    for q, val in zip(scale["questions"], values):
        qid = q.get("id")
        if qid in (12, 24, 62):
            continue  # 测谎题单独处理
        d = q.get("dimension")
        if d:
            dims.setdefault(d, {"total": 0, "count": 0})
            dims[d]["total"] += val
            dims[d]["count"] += 1
    avg = {k: v["total"] / max(v["count"], 1) for k, v in dims.items()}
    lie_vals = []
    for q, val in zip(scale["questions"], values):
        if q.get("id") in (12, 24, 62):
            lie_vals.append(val)
    lie_avg = sum(lie_vals) / len(lie_vals) if lie_vals else 3
    valid = lie_avg <= 4
    total_avg = sum(avg.values()) / max(len(avg), 1) if avg else 0
    total_score = round(total_avg / 5.0 * 100)
    level = "情绪智力水平优秀" if total_avg >= 4.2 else \
        "情绪智力水平良好" if total_avg >= 3.8 else \
        "情绪智力水平中等" if total_avg >= 3.2 else \
        "情绪智力水平偏低" if total_avg >= 2.5 else "情绪智力水平待提升"
    dims_out = {}
    for k, a in avg.items():
        info = dims_info.get(k, {})
        dims_out[k] = {"name": info.get("name", k), "avg": _round(a),
                       "desc": info.get("desc", "")}
    dims_out["lie"] = {"name": "测谎指数", "value": _round(lie_avg),
                       "desc": "测谎指数 ≤ 4 视为作答有效" if valid else
                       "测谎指数偏高，结果可信度有限，建议放松后重新作答"}
    return {"total": total_score, "max": 100, "level": level,
            "advice": "总分 {} / 100（{}）。{}".format(
                total_score, level,
                "作答有效。" if valid else "本次作答的测谎题得分偏高，结果仅供参考。"),
            "severity": 1 - total_avg / 5.0, "dimensions": dims_out,
            "total_label": "情绪智力指数", "valid": valid}


def _score_mbti(scale, values, detail):
    pairs = {"EI": ("E", "I"), "SN": ("S", "N"), "TF": ("T", "F"),
             "JP": ("J", "P")}
    score = {k: {"first": 0, "second": 0, "count": 0} for k in pairs}
    for q, val in zip(scale["questions"], values):
        d = q.get("dimension")
        if d not in score:
            continue
        if q.get("reverse"):
            score[d]["first"] += 4 - val
            score[d]["second"] += val
        else:
            score[d]["first"] += val
            score[d]["second"] += 4 - val
        score[d]["count"] += 1
    threshold = 2.5 / 4
    letters = {}
    dims_out = {}
    for key, (a, b) in pairs.items():
        cur = score[key]
        total = max(cur["first"] + cur["second"], 1)
        ratio = cur["first"] / total
        first_avg = cur["first"] / cur["count"] if cur["count"] else 2
        second_avg = cur["second"] / cur["count"] if cur["count"] else 2
        letter = a if ratio > threshold else b
        strength = min(abs(ratio - threshold) / threshold, 1)
        letters[key] = letter
        dims_out[key] = {"name": "{}-{}".format(a, b),
                         "letter": letter, "first_ratio": _round(ratio * 100, 0),
                         "first_avg": _round(first_avg), "second_avg": _round(second_avg),
                         "strength": _round(strength),
                         "desc": "偏好 {}（{}%），倾向强度 {}".format(
                             letter, round(ratio * 100), "明显" if strength > 0.5 else "一般")}
    mbti_type = "".join(letters[k] for k in ("EI", "SN", "TF", "JP"))
    names = {
        "ISTJ": "物流师", "ISFJ": "守卫者", "INFJ": "提倡者", "INTJ": "建筑师",
        "ISTP": "鉴赏家", "ISFP": "探险家", "INFP": "调停者", "INTP": "逻辑学家",
        "ESTP": "企业家", "ESFP": "表演者", "ENFP": "竞选者", "ENTP": "辩论家",
        "ESTJ": "总经理", "ESFJ": "执政官", "ENFJ": "主人公", "ENTJ": "指挥官",
    }
    type_name = names.get(mbti_type, mbti_type)
    total_avg = sum(score[k]["first"] / max(score[k]["count"], 1)
                    for k in pairs) / 4.0
    dims_out["type"] = {"name": "人格类型", "value": mbti_type,
                        "desc": type_name}
    return {"total": round(total_avg * 25), "max": 100,
            "level": "{}{}".format(mbti_type, type_name),
            "advice": "你的 MBTI 类型倾向为 {}（{}）。MBTI 描述的是偏好而非能力，"
                      "没有好坏之分，仅供参考。".format(mbti_type, type_name),
            "severity": 0.0, "dimensions": dims_out, "total_label": "类型倾向",
            "mbti_type": mbti_type, "mbti_type_name": type_name}


def _score_16pf(scale, values, detail):
    factors = ["A", "B", "C", "E", "F", "G", "H", "I", "L", "M", "N", "O",
               "Q1", "Q2", "Q3", "Q4"]
    raw = {f: 0 for f in factors}
    cnt = {f: 0 for f in factors}
    for q, val in zip(scale["questions"], values):
        f = q.get("factor")
        if f not in raw:
            continue
        cnt[f] += 1
        if f == "B":
            raw[f] += 1 if q.get("correct_answer") is not None and val == q["correct_answer"] else 0
        else:
            raw[f] += val
    std = {}
    for f in factors:
        if cnt[f]:
            maxp = cnt[f] if f == "B" else cnt[f] * 2
            std[f] = min(10, max(1, round(raw[f] / maxp * 9) + 1))
        else:
            std[f] = 5
    s = std
    x1 = (38 + 2 * s["L"] + 3 * s["O"] + 4 * s["Q4"] - 2 * s["C"] - 2 * s["H"] - 2 * s["Q3"]) / 10
    x2 = (2 * s["A"] + 3 * s["E"] + 4 * s["F"] + 5 * s["H"] - 2 * s["Q2"] - 11) / 10
    x3 = (77 + 2 * s["C"] + 2 * s["E"] + 2 * s["F"] + 2 * s["N"] - 4 * s["A"] - 6 * s["I"] - 2 * s["M"]) / 10
    x4 = (4 * s["E"] + 3 * s["H"] + 4 * s["Q3"] - 3 * s["A"] - 6 * s["G"] - 2 * s["I"] - 2 * s["O"]) / 10
    secondary = {
        "X1": {"name": "适应与焦虑", "value": _round(x1),
               "level": "高焦虑" if x1 > 5.5 else "低焦虑" if x1 < 4.5 else "中等焦虑"},
        "X2": {"name": "内向与外向", "value": _round(x2),
               "level": "外向型" if x2 > 5.5 else "内向型" if x2 < 4.5 else "中间型"},
        "X3": {"name": "感情用事与安详机警", "value": _round(x3),
               "level": "安详机警型" if x3 > 5.5 else "感情用事型" if x3 < 4.5 else "中间型"},
        "X4": {"name": "怯懦与果断", "value": _round(x4),
               "level": "果断型" if x4 > 5.5 else "怯懦型" if x4 < 4.5 else "中间型"},
    }
    info = scale.get("factors", {})
    dims_out = {}
    for f in factors:
        fi = info.get(f, {})
        dims_out[f] = {"name": fi.get("name", f), "value": std[f],
                       "high": fi.get("high", ""), "low": fi.get("low", ""),
                       "raw": raw[f]}
    for k, v in secondary.items():
        dims_out[k] = v
    top = sorted(factors, key=lambda f: -std[f])[:3]
    top_name = "、".join(info.get(f, {}).get("name", f) for f in top)
    return {"total": round(sum(std[f] for f in factors) / 16.0, 1), "max": 10,
            "level": "人格特质突出：{}".format(top_name),
            "advice": "得分最高的三项人格特质：{}。16PF 描述的是稳定的行为风格，"
                      "不存在「好」或「坏」，仅供参考。".format(top_name),
            "severity": 0.0, "dimensions": dims_out, "total_label": "平均标准分",
            "top_factors": top_name}


def _score_epq(scale, values, detail):
    names = {"E": "内外向", "N": "神经质", "P": "精神质", "L": "掩饰性（效度）"}
    raw = {k: 0 for k in names}
    cnt = {k: 0 for k in names}
    for q, val in zip(scale["questions"], values):
        d = q.get("dimension")
        if d in raw:
            raw[d] += val
            cnt[d] += 1
    if scale["key"] == "epq-rsc":
        norms = {"E": (7.5, 3.2), "N": (6.8, 3.5), "P": (3.5, 2.5), "L": (8.5, 3.8)}
    else:
        norms = {"E": (11.16, 4.41), "N": (9.06, 4.75), "P": (5.68, 3.09), "L": (12.51, 3.57)}
    t = {}
    for k, (m, sd) in norms.items():
        t[k] = min(100, max(0, round(50 + 10 * (raw[k] - m) / sd)))

    def desc(ts):
        if ts >= 61.5:
            return "典型高分"
        if ts >= 56.7:
            return "倾向高分"
        if ts <= 38.5:
            return "典型低分"
        if ts <= 43.3:
            return "倾向低分"
        return "中间型"

    max_t = max(t["E"], t["N"], t["P"])
    primary = "E" if t["E"] == max_t else "N" if t["N"] == max_t else "P"
    dims_out = {}
    for k in names:
        extra = "（回答可能存在掩饰倾向，结果仅供参考）" if k == "L" and t[k] >= 60 else ""
        dims_out[k] = {"name": names[k], "t": t[k], "raw": raw[k],
                       "desc": "{}{}".format(desc(t[k]), extra)}
    return {"total": t["E"], "max": 100,
            "level": "{}型人格".format(names[primary]),
            "advice": "E 内外向 {}、N 神经质 {}、P 精神质 {}。T 分数以中国常模换算，"
                      "50 为平均水平，仅供参考。".format(desc(t["E"]), desc(t["N"]), desc(t["P"])),
            "severity": 0.0, "dimensions": dims_out, "total_label": "E 量表 T 分"}


_SCORERS = {
    "sum": _score_sum,
    "sds": _score_zung,
    "sas": _score_zung,
    "scl90": _score_scl90,
    "sccs": _score_sccs,
    "temperament": _score_temperament,
    "bpns": _score_bpns,
    "ipip-eis": _score_ipip,
    "mbti": _score_mbti,
    "16pf": _score_16pf,
    "epq": _score_epq,
}


def score_scale(scale_key, answers):
    """按量表计分：answers 为每题选项索引，返回完整结果 dict"""
    scale = SCALES_BY_KEY.get(scale_key)
    if not scale:
        raise ValueError("未知量表: {}".format(scale_key))
    values = []
    detail = []
    for i, q in enumerate(scale["questions"]):
        vals = _opt_values(scale, q)
        labels = _opt_labels(scale, q)
        idx = int(answers[i]) if i < len(answers) else 0
        idx = max(0, min(len(vals) - 1, idx))
        val = _rev_value(q, vals[idx], vals)
        values.append(val)
        detail.append({
            "q": q.get("text", ""),
            "answer": idx,
            "value": val,
            "text": labels[idx] if idx < len(labels) else "",
        })
    stype = scale["scoring"].get("type", "sum")
    scorer = _SCORERS.get(stype)
    if not scorer:
        raise ValueError("不支持的计分类型: {}".format(stype))
    result = scorer(scale, values, detail)
    result.update({
        "scale_key": scale_key,
        "scale_name": scale["name"],
        "category": scale.get("category", ""),
        "answers": detail,
        "created_at": time.strftime("%Y-%m-%d %H:%M"),
    })
    crisis = False
    crisis_items = []
    ci = scale.get("crisis_question", -1)
    if ci >= 1:
        for i, q in enumerate(scale["questions"]):
            if q.get("id") == ci and detail[i].get("answer", 0) > 0:
                crisis = True
                crisis_items.append(q.get("text", ""))
    result["crisis"] = crisis
    result["crisis_items"] = crisis_items
    result.setdefault("advice", "")
    return result


def add_assessment(scale_key, answers):
    """完成一次评测：计分并存入 assessment.json，返回结果 dict"""
    result = score_scale(scale_key, answers)
    data = load_assessments()
    data.setdefault("records", []).append(result)
    save_assessments(data)
    return result


def delete_assessment(index):
    """按列表倒序索引删除（历史面板从新到旧，索引 0 = 最新一条）"""
    data = load_assessments()
    records = data.get("records", [])
    if 0 <= index < len(records):
        records.pop(index)
        save_assessments(data)
        return True
    return False


def recent_assessment():
    """最近一次评测结果（没有则返回 None）"""
    records = load_assessments().get("records", [])
    return records[-1] if records else None


def summary_text(result):
    """把一条评测结果压缩成一行摘要（供 AI 上下文 / 麟的回应使用）"""
    if not result:
        return ""
    total = result.get("total", 0)
    total_label = result.get("total_label", "分")
    if result.get("total_label") == "总均分":
        return "{} {} / {}（{}）·{}".format(
            result.get("scale_name", ""), total, result.get("max", 0),
            total_label, result.get("level", ""))
    return "{} {}分（满分{}）·{}".format(
        result.get("scale_name", ""), total,
        result.get("max", 0), result.get("level", ""))
