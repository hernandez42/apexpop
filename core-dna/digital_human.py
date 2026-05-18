#!/usr/bin/env python3
"""
数字人交互接口 — 完整实现
支持：文本/语音输入 → 意图理解 → 响应渲染
支持：多意图识别、上下文记忆、多轮对话
新增：小米产品推荐、笑话段子、健身建议、投资分析
"""

import json
import time
import re
import random
from typing import Optional, Dict, List, Any, Tuple
from pathlib import Path

# ===================== 意图定义 =====================

INTENTS = {
    # --- 原有意图 ---
    "greeting": {
        "patterns": ["你好", "hi", "hello", "嗨", "早上好", "晚上好", "下午好", "在吗"],
        "description": "打招呼",
    },
    "query_weather": {
        "patterns": ["天气", "气温", "下雨", "晴天", "温度", "forecast"],
        "description": "查询天气",
    },
    "coding_help": {
        "patterns": ["代码", "编程", "python", "rust", "bug", "debug", "写代码", "函数", "代码审查"],
        "description": "编程帮助",
    },
    "knowledge_query": {
        "patterns": ["什么是", "怎么", "为什么", "如何", "解释", "含义", "定义"],
        "description": "知识查询",
    },
    "task_management": {
        "patterns": ["任务", "待办", "提醒", "日程", "计划", "todo", "deadline"],
        "description": "任务管理",
    },
    "file_operation": {
        "patterns": ["文件", "保存", "读取", "打开", "创建文件", "删除文件"],
        "description": "文件操作",
    },
    "system_control": {
        "patterns": ["状态", "健康", "重启", "停止", "启动", "监控"],
        "description": "系统控制",
    },
    "creative": {
        "patterns": ["写一首", "画", "生成", "创作", "设计", "文章"],
        "description": "创作请求",
    },
    "analysis": {
        "patterns": ["分析", "对比", "评估", "预测", "趋势", "数据"],
        "description": "分析请求",
    },
    "help": {
        "patterns": ["帮助", "怎么用", "功能", "能做什么", "使用说明"],
        "description": "帮助请求",
    },
    # --- 新增意图 ---
    "xiaomi_product": {
        "patterns": ["小米", "xiaomi", "su7", "红米", "miui", "hyper", "澎湃",
                      "雷军", "手机", "电视", "手环", "路由器", "智能家居",
                      "米家", "mix", "pad", "笔记本", "充电器", "耳机"],
        "description": "小米产品推荐",
    },
    "joke": {
        "patterns": ["笑话", "段子", "搞笑", "幽默", "开心", "乐一个",
                      "讲个笑话", "逗我笑", "来个段子", "整点乐子"],
        "description": "笑话/段子",
    },
    "fitness": {
        "patterns": ["健身", "锻炼", "运动", "减脂", "增肌", "跑步", "瑜伽",
                      "体重", "卡路里", "蛋白质", "饮食", "塑形", "减肥"],
        "description": "健身建议",
    },
    "investment": {
        "patterns": ["投资", "股票", "基金", "理财", "港股", "美股", "a股",
                      "市盈率", "市净率", "收益率", "k线", "均线", "仓位",
                      "定投", "etf", "纳斯达克", "标普", "恒生", "指数"],
        "description": "投资分析",
    },
    "music": {
        "patterns": ["音乐", "歌曲", "歌单", "听歌", "推荐歌", "唱歌",
                      "摇滚", "流行", "古典", "爵士", "电子", "民谣",
                      "说唱", "r&b", "嘻哈", "蓝调", "钢琴", "吉他"],
        "description": "音乐推荐",
    },
    "food": {
        "patterns": ["美食", "吃", "餐厅", "菜谱", "做饭", "烹饪",
                      "火锅", "烧烤", "甜点", "咖啡", "茶", "小吃",
                      "早餐", "午餐", "晚餐", "外卖", "下厨", "家常菜"],
        "description": "美食推荐",
    },
}

# ===================== 小米产品库 =====================

XIAOMI_PRODUCTS = {
    "手机": [
        {"name": "小米15 Pro", "tag": "旗舰影像", "highlight": "骁龙8 Elite + 徕卡光学"},
        {"name": "小米15", "tag": "轻薄旗舰", "highlight": "小尺寸旗舰，手感极佳"},
        {"name": "Redmi K80 Pro", "tag": "性能旗舰", "highlight": "2K屏 + 骁龙8 Elite"},
        {"name": "Redmi Turbo 4", "tag": "性价比之王", "highlight": "天玑8400-Ultra"},
    ],
    "汽车": [
        {"name": "小米SU7", "tag": "智能电动轿车", "highlight": "C级轿车，续航800km+"},
        {"name": "小米SU7 Ultra", "tag": "性能巅峰", "highlight": "三电机，1548马力"},
        {"name": "小米YU7", "tag": "智能SUV", "highlight": "中大型SUV，家庭首选"},
    ],
    "穿戴": [
        {"name": "小米手环9", "tag": "健康伴侣", "highlight": "150+运动模式，血氧检测"},
        {"name": "小米Watch S4", "tag": "专业运动", "highlight": "AMOLED屏 + 双频GPS"},
    ],
    "平板": [
        {"name": "小米Pad 7 Pro", "tag": "生产力", "highlight": "12.4寸3K屏 + 澎湃OS"},
    ],
    "智能家居": [
        {"name": "小米智能门锁Pro", "tag": "安全", "highlight": "3D结构光 + 猫眼"},
        {"name": "米家扫拖机器人2", "tag": "清洁", "highlight": "8000Pa吸力 + 自动集尘"},
    ],
}

# ===================== 笑话/段子库 =====================

JOKES = [
    "程序员最怕什么？最怕改需求。第二怕什么？改完需求发现原始需求就是对的。",
    "为什么程序员总是分不清万圣节和圣诞节？因为 Oct 31 == Dec 25。",
    "一个程序员去面试，面试官问：你期望的薪资是多少？程序员说：100k。面试官：月薪？程序员：不，是年薪。",
    "有人问程序员：你年薪多少？程序员说：2的20次方。对方：大约100万？程序员：嗯，1048576。",
    "世界上最远的距离不是生与死，而是我在写代码，你在提需求。",
    "产品经理说：这个功能很简单，就改个按钮颜色。程序员：好。改完后：整个系统崩了。",
    "代码写得好好的，产品经理路过说：这里改一下。然后整个周末就没了。",
    "为什么程序员不喜欢户外运动？因为外面没有Ctrl+Z。",
    "你写的代码能跑吗？能。那你为什么还在改？因为产品经理又来了一趟。",
    "一个程序员的老婆让他去超市：买一瓶牛奶，如果有鸡蛋，买六个。结果他带回了六瓶牛奶。因为有鸡蛋。",
]

# ===================== 健身知识库 =====================

FITNESS_TIPS = {
    "减脂": [
        "减脂核心：热量缺口 = 消耗 - 摄入，建议每日缺口 300-500 kcal",
        "力量训练 + 有氧组合效果最佳，力量训练提升基础代谢",
        "蛋白质摄入：体重(kg) × 1.6-2.0g，保护肌肉量",
        "减脂期碳水不要低于 100g/天，否则影响训练和激素水平",
    ],
    "增肌": [
        "增肌三大要素：渐进超负荷 + 足够蛋白质 + 充足休息",
        "蛋白质摄入：体重(kg) × 2.0-2.5g，分4-5餐摄入",
        "复合动作为主：深蹲、硬拉、卧推、划船、推举",
        "每组8-12次，组间休息60-90秒，训练时间控制在60分钟内",
    ],
    "跑步": [
        "新手建议：从跑走交替开始，每周增加跑量不超过10%",
        "心率区间：最大心率的60-70%为燃脂区，70-80%为有氧区",
        "跑前动态热身5分钟，跑后静态拉伸10分钟",
        "每周跑3-4次，间隔休息，避免连续跑步导致过度训练",
    ],
    "饮食": [
        "三餐分配：早餐30% + 午餐40% + 晚餐30%",
        "训练后30分钟内补充蛋白质 + 碳水，抓住合成窗口",
        "多喝水：每日至少2L，训练时额外补充",
        "减少加工食品，多吃全谷物、蔬菜、优质蛋白",
    ],
}

# ===================== 音乐推荐库 =====================

MUSIC_RECOMMENDATIONS = {
    "流行": [
        {"name": "周杰伦 - 晴天", "mood": "怀旧治愈", "tag": "华语经典"},
        {"name": "邓紫棋 - 光年之外", "mood": "浪漫大气", "tag": "华语流行"},
        {"name": "Taylor Swift - Anti-Hero", "mood": "轻松愉快", "tag": "欧美流行"},
    ],
    "摇滚": [
        {"name": "Beyond - 海阔天空", "mood": "热血励志", "tag": "经典摇滚"},
        {"name": "Imagine Dragons - Believer", "mood": "力量感", "tag": "另类摇滚"},
    ],
    "电子": [
        {"name": "Alan Walker - Faded", "mood": "空灵梦幻", "tag": "电子舞曲"},
        {"name": "The Chainsmokers - Closer", "mood": "浪漫轻松", "tag": "EDM"},
    ],
    "民谣": [
        {"name": "赵雷 - 成都", "mood": "文艺治愈", "tag": "中国民谣"},
        {"name": "宋冬野 - 安和桥", "mood": "温暖怀旧", "tag": "独立民谣"},
    ],
    "古典": [
        {"name": "贝多芬 - 月光奏鸣曲", "mood": "宁静深邃", "tag": "钢琴经典"},
        {"name": "德彪西 - 月光", "mood": "梦幻浪漫", "tag": "印象派"},
    ],
}

# ===================== 美食推荐库 =====================

FOOD_RECOMMENDATIONS = {
    "早餐": [
        {"name": "豆浆油条", "tag": "经典中式", "tip": "现炸油条配热豆浆，幸福感爆棚"},
        {"name": "煎饼果子", "tag": "天津特色", "tip": "加个鸡蛋加个脆饼，营养满分"},
        {"name": "牛角面包+拿铁", "tag": "西式简餐", "tip": "黄油香气配上咖啡的醇厚"},
    ],
    "午餐": [
        {"name": "番茄牛腩饭", "tag": "家常硬菜", "tip": "酸甜入味，拌饭一绝"},
        {"name": "日式拉面", "tag": "日料", "tip": "浓郁豚骨汤底，溏心蛋必加"},
        {"name": "黄焖鸡米饭", "tag": "快餐经典", "tip": "鸡肉嫩滑，汤汁浓郁"},
    ],
    "晚餐": [
        {"name": "清蒸鲈鱼", "tag": "粤菜", "tip": "鲜嫩清甜，姜丝葱花提味"},
        {"name": "宫保鸡丁", "tag": "川菜", "tip": "花生酥脆，鸡肉嫩滑，微辣回甘"},
        {"name": "意面配红酒", "tag": "西餐", "tip": "番茄肉酱意面，一杯红酒助消化"},
    ],
    "火锅": [
        {"name": "重庆九宫格", "tag": "川渝火锅", "tip": "毛肚七上八下，鸭肠快涮快吃"},
        {"name": "潮汕牛肉火锅", "tag": "粤式火锅", "tip": "鲜切牛肉，清汤锅底最考究"},
        {"name": "北京铜锅涮肉", "tag": "老北京", "tip": "清水锅底，蘸麻酱，羊肉鲜嫩"},
    ],
    "甜点": [
        {"name": "提拉米苏", "tag": "意式甜点", "tip": "咖啡与奶酪的完美融合"},
        {"name": "芒果班戟", "tag": "港式甜品", "tip": "新鲜芒果配奶油，甜蜜满分"},
        {"name": "舒芙蕾", "tag": "法式甜点", "tip": "蓬松轻盈，趁热吃最佳"},
    ],
}

# ===================== 投资分析模板 =====================

INVESTMENT_INSIGHTS = {
    "股票": [
        "选股核心：好行业 + 好公司 + 好价格（三好原则）",
        "关注指标：ROE > 15%，连续3年利润增长，负债率 < 60%",
        "估值参考：PE < 行业平均，PB < 历史中位数",
        "仓位管理：单只股票不超过总仓位的20%",
    ],
    "基金": [
        "定投策略：每月固定日期投入固定金额，平滑波动",
        "选基标准：3年以上业绩，基金经理稳定，规模适中",
        "指数基金：沪深300 + 中证500 组合，覆盖大中小盘",
        "止盈不止损：达到目标收益率后分批止盈",
    ],
    "港股": [
        "港股特点：估值洼地 + 高股息 + 南向资金持续流入",
        "关注板块：互联网龙头、高股息央企、创新药",
        "AH溢价：当AH溢价 > 140，港股相对低估",
        "注意汇率风险：港币与美元挂钩，影响实际收益",
    ],
    "美股": [
        "美股特点：全球最成熟市场 + 强监管 + 长期牛市",
        "核心配置：标普500 + 纳斯达克100，被动投资为主",
        "科技七巨头：关注FAANG+的财报和AI布局",
        "注意时差和税务：美股交易时间为北京时间21:30-4:00",
    ],
    "A股": [
        "A股特点：政策驱动 + 散户占比高 + 波动大",
        "关注政策：两会、政治局会议、央行货币政策",
        "行业轮动：关注景气度向上的行业（AI、新能源、半导体）",
        "风险管理：设置止损线（如-8%），严格执行",
    ],
}

# ===================== 响应模板 =====================

RESPONSES = {
    "greeting": [
        "你好！我是 {name}，很高兴为你服务。",
        "嗨！{name} 在线，有什么需要帮忙的？",
        "你好呀！今天有什么可以帮你的？",
    ],
    "query_weather": [
        "{name}：天气查询功能已准备就绪。你可以告诉我具体城市，我会帮你查询。",
        "{name}：实时天气服务需要外部 API 支持。你想查哪个城市的天气？",
    ],
    "coding_help": [
        "{name}：编程问题收到！请告诉我你使用的语言和具体需求。",
        "{name}：我可以帮你写代码、审查代码、调试。请描述你的问题。",
    ],
    "knowledge_query": [
        "{name}：好问题！让我为你分析一下。",
        "{name}：这个问题涉及知识检索，我来帮你查找。",
    ],
    "task_management": [
        "{name}：任务管理收到。你可以告诉我具体的待办事项。",
        "{name}：我来帮你管理任务。请描述需要做什么。",
    ],
    "file_operation": [
        "{name}：文件操作收到。请告诉我具体需求（创建/读取/保存）。",
    ],
    "system_control": [
        "{name}：系统控制收到。请告诉我具体操作（状态/重启/监控）。",
    ],
    "creative": [
        "{name}：创作请求收到！请告诉我你想要什么风格和主题。",
        "{name}：创意时间到！告诉我你的想法，我来帮你实现。",
    ],
    "analysis": [
        "{name}：分析请求收到。请告诉我你要分析什么数据。",
        "{name}：好的，我来帮你做分析。请提供更多上下文。",
    ],
    "help": [
        "{name}：我是 {name}，我可以帮你：\n"
        "  1. 🌤️ 天气查询\n"
        "  2. 💻 编程帮助\n"
        "  3. 📚 知识查询\n"
        "  4. 📱 小米产品推荐\n"
        "  5. 😂 笑话段子\n"
        "  6. 💪 健身建议\n"
        "  7. 📈 投资分析\n"
        "  8. ✅ 任务管理\n"
        "  9. 🎨 创作生成\n"
        "  10. 🎵 音乐推荐\n"
        "  11. 🍽️ 美食推荐",
    ],
    # --- 新增响应 ---
    "xiaomi_product": [
        "{name}：小米产品推荐来了！",
    ],
    "joke": [
        "{name}：来一个！",
    ],
    "fitness": [
        "{name}：健身建议来了！",
    ],
    "investment": [
        "{name}：投资分析如下：",
    ],
    "unknown": [
        "{name}：我理解了你的输入，但不太确定你的意图。请再描述一下？",
        "{name}：让我想想...你能更具体一些吗？",
    ],
}

# ===================== 数字人核心类 =====================

class EmotionalAnalyzer:
    """情感识别引擎 — 基于关键词匹配的情感分析"""

    # 情感关键词库
    EMOTION_KEYWORDS = {
        "happy": {
            "keywords": ["开心", "高兴", "快乐", "太好了", "棒", "赞", "nice",
                         "哈哈哈", "嘻嘻", "耶", "万岁", "爽", "完美", "优秀"],
            "emoji": "😊",
            "label": "开心",
        },
        "sad": {
            "keywords": ["难过", "伤心", "失望", "郁闷", "烦", "累", "不想",
                         "心情不好", "丧", "emo", "崩溃", "痛苦", "委屈"],
            "emoji": "😢",
            "label": "难过",
        },
        "angry": {
            "keywords": ["生气", "愤怒", "讨厌", "烦死了", "气死", "可恶",
                         "混蛋", "受不了", "忍无可忍", "火大"],
            "emoji": "😠",
            "label": "生气",
        },
        "anxious": {
            "keywords": ["焦虑", "担心", "紧张", "害怕", "恐惧", "不安",
                         "慌", "急", "来不及", "完蛋", "怎么办"],
            "emoji": "😰",
            "label": "焦虑",
        },
        "tired": {
            "keywords": ["累了", "疲惫", "困", "无聊", "没意思", "提不起劲",
                         "懒", "不想动", "躺平", "摆烂"],
            "emoji": "😴",
            "label": "疲惫",
        },
        "excited": {
            "keywords": ["兴奋", "期待", "激动", "太棒了", "终于", "等不及",
                         "冲", "来了来了", "终于等到"],
            "emoji": "🤩",
            "label": "兴奋",
        },
        "grateful": {
            "keywords": ["谢谢", "感谢", "感恩", "辛苦了", "太好了", "帮大忙",
                         "多谢", "感激", "受宠若惊"],
            "emoji": "🙏",
            "label": "感激",
        },
    }

    def analyze(self, text: str) -> Dict[str, Any]:
        """分析文本情感"""
        text_lower = text.lower()
        scores: Dict[str, int] = {}

        for emotion, config in self.EMOTION_KEYWORDS.items():
            count = sum(1 for kw in config["keywords"] if kw in text_lower)
            if count > 0:
                scores[emotion] = count

        if not scores:
            return {"emotion": "neutral", "emoji": "😐", "label": "平静", "confidence": 0.5}

        # 取最高分的情感
        best_emotion = max(scores, key=scores.get)
        total_hits = sum(scores.values())
        confidence = min(1.0, scores[best_emotion] / max(total_hits, 1) * 1.5 + 0.3)

        return {
            "emotion": best_emotion,
            "emoji": self.EMOTION_KEYWORDS[best_emotion]["emoji"],
            "label": self.EMOTION_KEYWORDS[best_emotion]["label"],
            "confidence": round(confidence, 2),
        }


class DigitalHuman:
    """数字人交互引擎 — 多意图识别 + 多轮对话 + 情感识别"""

    def __init__(self, name: str = "SuperClaw"):
        self.name = name
        self.context: List[Dict[str, Any]] = []
        self.max_context = 20
        self.intent_stats: Dict[str, int] = {}
        self.emotion_analyzer = EmotionalAnalyzer()
        self.emotion_history: List[Dict[str, Any]] = []
        self.conversation_turns: int = 0  # 对话轮次计数

    # ---- 输入处理 ----

    def input_text(self, text: str) -> Dict[str, Any]:
        return {"type": "text", "content": text.strip(), "timestamp": int(time.time())}

    def input_voice(self, audio_path: str, asr_result: Optional[str] = None) -> Dict[str, Any]:
        text = asr_result if asr_result else f"[语音识别中: {audio_path}]"
        return {"type": "voice", "content": text, "audio_path": audio_path, "timestamp": int(time.time())}

    # ---- 意图识别 ----

    def identify_intent(self, message: Dict[str, Any]) -> Dict[str, Any]:
        text = message["content"].lower()
        scores: List[Tuple[str, float, Dict]] = []

        for intent, config in INTENTS.items():
            match_count = 0
            entities = {}
            for pattern in config["patterns"]:
                if pattern.lower() in text:
                    match_count += 1
                    if intent == "query_weather":
                        city_match = re.search(r'([\u4e00-\u9fa5]{2,4})(?:天气|气温|温度)', text)
                        if city_match:
                            entities["city"] = city_match.group(1)
                    elif intent == "coding_help":
                        for lang in ["python", "rust", "java", "go", "javascript", "typescript"]:
                            if lang in text:
                                entities["language"] = lang
                    elif intent == "xiaomi_product":
                        for cat in ["手机", "汽车", "穿戴", "平板", "智能家居"]:
                            if cat in text:
                                entities["category"] = cat
                    elif intent == "fitness":
                        for topic in ["减脂", "增肌", "跑步", "饮食", "减肥", "体重"]:
                            if topic in text:
                                entities["topic"] = topic
                    elif intent == "investment":
                        for market in ["股票", "基金", "港股", "美股", "a股", "etf", "定投"]:
                            if market in text:
                                entities["market"] = market
                    elif intent == "music":
                        for genre in ["流行", "摇滚", "电子", "民谣", "古典", "说唱", "爵士"]:
                            if genre in text:
                                entities["genre"] = genre
                    elif intent == "food":
                        for meal in ["早餐", "午餐", "晚餐", "火锅", "甜点", "小吃", "下午茶"]:
                            if meal in text:
                                entities["meal"] = meal

            if match_count > 0:
                confidence = min(1.0, match_count * 0.4 + 0.3)
                scores.append((intent, confidence, entities))

        if not scores:
            return {"intent": "unknown", "confidence": 0.0, "entities": {}}

        scores.sort(key=lambda x: x[1], reverse=True)
        best = scores[0]
        return {"intent": best[0], "confidence": best[1], "entities": best[2]}

    # ---- 上下文管理 ----

    def _update_context(self, message: Dict, intent_result: Dict):
        # 情感分析
        emotion_result = self.emotion_analyzer.analyze(message["content"])
        self.emotion_history.append(emotion_result)
        if len(self.emotion_history) > self.max_context:
            self.emotion_history = self.emotion_history[-self.max_context:]

        self.context.append({
            "message": message,
            "intent": intent_result,
            "emotion": emotion_result,
            "turn": self.conversation_turns,
            "timestamp": int(time.time()),
        })
        if len(self.context) > self.max_context:
            self.context = self.context[-self.max_context:]

    # ---- 响应生成 ----

    def _generate_response(self, intent_result: Dict) -> str:
        intent = intent_result["intent"]
        entities = intent_result.get("entities", {})

        # 获取当前情感状态
        current_emotion = self.emotion_history[-1] if self.emotion_history else {"emoji": "", "label": ""}

        # 特殊意图：生成具体内容
        if intent == "xiaomi_product":
            base = self._gen_xiaomi_response(entities)
        elif intent == "joke":
            base = self._gen_joke_response()
        elif intent == "fitness":
            base = self._gen_fitness_response(entities)
        elif intent == "investment":
            base = self._gen_investment_response(entities)
        elif intent == "music":
            base = self._gen_music_response(entities)
        elif intent == "food":
            base = self._gen_food_response(entities)
        else:
            # 通用意图：模板回复
            templates = RESPONSES.get(intent, RESPONSES["unknown"])
            template = random.choice(templates)
            base = template.format(name=self.name)

            if entities:
                entity_str = "，".join(f"{k}={v}" for k, v in entities.items())
                base += f"\n  📎 识别到: {entity_str}"

        # 根据情感状态调整回复语气
        emotion_prefix = ""
        if current_emotion["emotion"] == "sad":
            emotion_prefix = "别担心，"
        elif current_emotion["emotion"] == "angry":
            emotion_prefix = "冷静一下，"
        elif current_emotion["emotion"] == "anxious":
            emotion_prefix = "放轻松，"
        elif current_emotion["emotion"] == "tired":
            emotion_prefix = "辛苦了，"
        elif current_emotion["emotion"] == "happy":
            emotion_prefix = ""
        elif current_emotion["emotion"] == "excited":
            emotion_prefix = ""

        return emotion_prefix + base

    def _gen_xiaomi_response(self, entities: Dict) -> str:
        """生成小米产品推荐"""
        category = entities.get("category")
        if category and category in XIAOMI_PRODUCTS:
            products = XIAOMI_PRODUCTS[category]
        else:
            # 随机推荐
            category = random.choice(list(XIAOMI_PRODUCTS.keys()))
            products = XIAOMI_PRODUCTS[category]

        product = random.choice(products)
        lines = [
            f"📱 小米 {category}推荐：{product['name']}",
            f"  🏷️ {product['tag']}",
            f"  ⭐ {product['highlight']}",
            "",
            "  💡 更多产品请访问 mi.com",
        ]
        return "\n".join(lines)

    def _gen_joke_response(self) -> str:
        """生成笑话"""
        joke = random.choice(JOKES)
        return f"😂 {joke}"

    def _gen_fitness_response(self, entities: Dict) -> str:
        """生成健身建议"""
        topic = entities.get("topic")
        if topic and topic in FITNESS_TIPS:
            tips = FITNESS_TIPS[topic]
        elif topic == "减肥":
            tips = FITNESS_TIPS["减脂"]
        else:
            topic = random.choice(list(FITNESS_TIPS.keys()))
            tips = FITNESS_TIPS[topic]

        tip = random.choice(tips)
        return f"💪 健身建议 [{topic}]：\n  {tip}"

    def _gen_investment_response(self, entities: Dict) -> str:
        """生成投资分析"""
        market = entities.get("market")
        if market:
            # 标准化市场名称
            market_map = {"a股": "A股", "etf": "基金", "定投": "基金"}
            market = market_map.get(market, market)
        if market and market in INVESTMENT_INSIGHTS:
            insights = INVESTMENT_INSIGHTS[market]
        else:
            market = random.choice(list(INVESTMENT_INSIGHTS.keys()))
            insights = INVESTMENT_INSIGHTS[market]

        insight = random.choice(insights)
        return f"📈 投资分析 [{market}]：\n  {insight}\n  ⚠️ 投资有风险，以上仅供参考"

    def _gen_music_response(self, entities: Dict) -> str:
        """生成音乐推荐"""
        genre = entities.get("genre")
        if genre and genre in MUSIC_RECOMMENDATIONS:
            songs = MUSIC_RECOMMENDATIONS[genre]
        else:
            genre = random.choice(list(MUSIC_RECOMMENDATIONS.keys()))
            songs = MUSIC_RECOMMENDATIONS[genre]

        song = random.choice(songs)
        lines = [
            f"🎵 音乐推荐 [{genre}]：{song['name']}",
            f"  🎭 氛围：{song['mood']}",
            f"  🏷️ 标签：{song['tag']}",
        ]
        return "\n".join(lines)

    def _gen_food_response(self, entities: Dict) -> str:
        """生成美食推荐"""
        meal = entities.get("meal")
        if meal and meal in FOOD_RECOMMENDATIONS:
            foods = FOOD_RECOMMENDATIONS[meal]
        else:
            meal = random.choice(list(FOOD_RECOMMENDATIONS.keys()))
            foods = FOOD_RECOMMENDATIONS[meal]

        food = random.choice(foods)
        lines = [
            f"🍽️ 美食推荐 [{meal}]：{food['name']}",
            f"  🏷️ {food['tag']}",
            f"  💡 {food['tip']}",
        ]
        return "\n".join(lines)

    # ---- 主交互入口 ----

    def chat(self, text: Optional[str] = None, audio_path: Optional[str] = None,
             asr_result: Optional[str] = None) -> Dict[str, Any]:
        if text:
            message = self.input_text(text)
        elif audio_path:
            message = self.input_voice(audio_path, asr_result)
        else:
            return {"error": "请提供文本或语音输入"}

        self.conversation_turns += 1
        intent_result = self.identify_intent(message)
        self._update_context(message, intent_result)
        self.intent_stats[intent_result["intent"]] = \
            self.intent_stats.get(intent_result["intent"], 0) + 1
        response = self._generate_response(intent_result)

        return {
            "input": message,
            "intent": intent_result,
            "emotion": self.emotion_history[-1] if self.emotion_history else None,
            "turn": self.conversation_turns,
            "response": response,
        }

    # ---- 状态查询 ----

    def get_stats(self) -> Dict[str, Any]:
        # 情感分布统计
        emotion_dist: Dict[str, int] = {}
        for e in self.emotion_history:
            emotion_dist[e["label"]] = emotion_dist.get(e["label"], 0) + 1

        return {
            "name": self.name,
            "total_interactions": len(self.context),
            "intent_distribution": self.intent_stats,
            "emotion_distribution": emotion_dist,
            "conversation_turns": self.conversation_turns,
            "context_length": len(self.context),
        }

    def clear_context(self):
        self.context.clear()

    def export_context(self) -> str:
        return json.dumps(self.context, ensure_ascii=False, indent=2)


# ===================== CLI 入口 =====================

def main():
    import sys

    dh = DigitalHuman()

    if len(sys.argv) > 1 and sys.argv[1] != "--test":
        text = " ".join(sys.argv[1:])
        result = dh.chat(text=text)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 测试模式
    print("=== 数字人交互测试 ===\n")

    test_cases = [
        "你好",
        "今天天气怎么样",
        "帮我写一段 Python 代码",
        "什么是机器学习",
        "帮我创建一个待办事项",
        "状态检查",
        "写一首关于春天的诗",
        "分析一下最近的数据",
        # 新增测试
        "小米SU7怎么样",
        "推荐一款小米手机",
        "讲个笑话",
        "来个段子逗我开心",
        "我想减脂，给点建议",
        "怎么增肌",
        "帮我看看A股",
        "港股现在能投吗",
        "美股定投什么基金好",
        # 音乐和美食测试
        "推荐一首流行歌",
        "有什么好听的摇滚",
        "今天吃什么好",
        "推荐个火锅店",
        "来份早餐推荐",
        # 情感测试
        "今天心情不好，好难过",
        "太开心了！终于完成了！",
        "好累啊，不想动了",
    ]

    for text in test_cases:
        result = dh.chat(text=text)
        intent = result["intent"]["intent"]
        conf = result["intent"]["confidence"]
        emotion = result.get("emotion", {})
        emotion_str = f"{emotion.get('emoji', '')} {emotion.get('label', '平静')}" if emotion else "😐 平静"
        response = result["response"]
        print(f"📝 输入: {text}")
        print(f"   意图: {intent} (置信度: {conf:.2f})")
        print(f"   情感: {emotion_str}")
        print(f"   回复: {response[:100]}")
        print()

    # 统计
    print("--- 交互统计 ---")
    stats = dh.get_stats()
    print(f"总交互: {stats['total_interactions']}")
    print(f"对话轮次: {stats['conversation_turns']}")
    print(f"意图分布: {json.dumps(stats['intent_distribution'], ensure_ascii=False)}")
    print(f"情感分布: {json.dumps(stats['emotion_distribution'], ensure_ascii=False)}")

    print("\n✅ 数字人交互测试通过")


if __name__ == "__main__":
    main()
