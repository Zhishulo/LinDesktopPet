# -*- coding: utf-8 -*-
"""本地保底台词引擎（无 API / API 故障时的 QQ 宠物式离线模式）。
纯函数，不依赖 GUI 与网络，便于单独维护和测试。"""
import random
import re
import time


def local_reply(text, mood="normal", weather="", weather_temp=""):
    """关键词规则离线回复：参数化语气（mood）、天气与温度，返回一句台词。"""
    t = (text or "").strip()
    tl = t.lower()
    # 时间问候
    if re.search(r"早安|早上好|早晨|morning", t) or "morning" in tl:
        return "早上好呀，幸歌！今天也要元气满满哦～"
    if re.search(r"晚安|睡吧|去睡|好困", t):
        return "晚安，幸歌～做个好梦，麟会守着你的！"
    if re.search(r"中午好|午安", t):
        return "中午好！记得吃午饭，麟会监督的哦～"
    if re.search(r"晚上好|傍晚好", t):
        return "晚上好，幸歌！今天辛苦啦～"
    # 打招呼
    if re.search(r"你好|哈喽|嗨|hello|hi|在吗|在不在", t) or "hello" in tl or " hi" in tl:
        return "在呀在呀！麟一直都在～幸歌今天想聊点什么？"
    # 身份
    if re.search(r"你(是|叫)(谁|什么)|你是谁|自我介绍|介绍一下你", t):
        return "麟是来自丰裕之角的小麒麟呀！喜欢甜食、喜欢晴朗的天气、最喜欢幸歌～"
    # 问麟在做什么
    if re.search(r"你在(干什么|做什么|干嘛)|在忙什么", t):
        return "麟在陪幸歌呀～顺便数数小金库里的金币，嘿嘿。"
    # 问麟饿不饿
    if re.search(r"你(饿不饿|吃了吗)|吃什么|吃饭", t):
        return "有点想吃布丁了……幸歌要喂麟吗？"
    # 问麟累不累
    if re.search(r"你(困不困|累不累)|要不要休息", t):
        return "麟不累！和幸歌聊天就是最好的休息啦～"
    # 问心情
    if re.search(r"你(开心|高兴)吗|心情怎么样|心情如何", t):
        return {"happy": "超开心！和幸歌在一起的时候最开心啦～",
                "normal": "心情平稳～和幸歌聊聊天就更好了！",
                "sad": "有一点点低落……不过幸歌和麟说话，就好多了！"}.get(mood, "心情平稳～")
    # 表白（放夸赞前）
    if re.search(r"我爱你|我喜欢你|爱你", t):
        return "呜哇！幸歌这么说，麟的脸好烫……麟也最喜欢幸歌啦！"
    # 夸麟
    if re.search(r"(好|真|最|很|太)(可爱|乖|萌|棒|聪明|厉害)|好乖|真乖", t):
        return "嘿嘿～被幸歌夸了，麟的尾巴都要翘到天上去了！"
    # 批评/委屈
    if any(k in t for k in ["讨厌", "笨", "蠢", "没用", "滚", "闭嘴", "烦"]):
        return "呜……麟会努力改进的！但幸歌不要真的生麟的气嘛……"
    # 问天气
    if re.search(r"天气|下雨|下雪|冷|热", t):
        if weather:
            return "外面{}，{}℃～出门记得照顾好自己哦！".format(weather, weather_temp or "?")
        return "麟今天没能连上天气站……但不管什么天气，麟都会陪着幸歌！"
    # 问时间/日期
    if re.search(r"几点了|什么时间|现在(几点|什么时候)|今天(几号|星期几|什么日子)", t):
        return "现在是{}，{}！麟的时间可是很准的～".format(
            time.strftime("%H:%M"), time.strftime("%Y年%m月%d日 %A"))
    # 问功能
    if re.search(r"你会什么|有什么功能|怎么玩|帮助|指令", t):
        return ("输入 #帮助 可以看全部指令～麟会聊天、报时、提醒日程、记笔记、"
                "陪你专注，还会记住你的喜好哦！")
    # 无聊
    if re.search(r"无聊|没意思|好闲|没事干", t):
        return "那麟陪幸歌猜拳或者掷骰子吧？还可以去商城看看有什么好玩的～"
    # 工作/学习
    if re.search(r"工作|加班|开会|写代码|作业|学习|考试|论文|报告", t):
        return "幸歌加油！麟会在旁边默默给你打气的～要不要开个专注时钟？"
    # 累/压力
    if re.search(r"累|辛苦|好烦|压力|难受", t):
        return "幸歌辛苦啦……要不要摸摸麟的头？麟的毛很软，烦恼会跑掉的哦。"
    # 感谢
    if re.search(r"谢谢|感谢|辛苦你", t):
        return "不客气～能帮到幸歌，麟超开心的！"
    # 告别
    if re.search(r"再见|拜拜|回见|see you|bye", t) or "bye" in tl:
        return "拜拜，幸歌！麟会一直在这里等你的～"
    # 默认
    return random.choice([
        "嗯嗯，麟在认真听呢～",
        "原来如此！人类的世界真是奇妙呀～",
        "幸歌说的事情，麟记在心里啦！",
        "嘿嘿，幸歌再多说一点嘛，麟想听！",
        "唔……麟想了想，觉得幸歌说得对！",
        "丰裕之角的风今天也很温柔呢～",
    ])
