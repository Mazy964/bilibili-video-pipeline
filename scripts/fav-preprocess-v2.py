#!/usr/bin/env python3
"""
fav-preprocess-v2.py — 本地预处理 V2：分区 + 关键词 + UP主
=========================================================
1. 加载分区数据 (fav-videos-enriched.json)
2. 用 tid→分区→文件夹映射做第一轮分类
3. 关键词规则做第二轮
4. UP主规则做第三轮
5. 输出覆盖率报告
"""

import json, os, re, sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(ROOT, "..", "cache")
VIDEOS_FILE = os.path.join(CACHE_DIR, "fav-videos.json")
ENRICHED_FILE = os.path.join(CACHE_DIR, "fav-videos-enriched.json")
PREPROCESS_OUT = os.path.join(CACHE_DIR, "fav-preprocess.json")
LEFT_FOR_LLM = os.path.join(CACHE_DIR, "fav-left-for-llm.json")
EXISTING_LLM = os.path.join(CACHE_DIR, "fav-classify.json")

# Import tid map
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tid_map import TID_MAP, PARTITION_TO_FOLDER, get_folder_for_tid

# ── Folder validation ──────────────────────────────────────
VALID_FOLDERS = {
    "下载", "牢啊", "量子", "低空", "鸿蒙玩法", "稍后", "时事", "骑行",
    "问题_科技", "科技创新", "美国", "新能源汽车", "93", "观察",
    "ai agent", "三国", "Mod", "DeepSeek", "机器人", "航空航天",
    "申遗", "军", "fpv", "奥特曼", "ppt", "amd", "高数", "黑神话",
    "vam", "科幻", "嵌入式", "软件", "网络", "编程", "网络(2)",
    "鸿蒙", "硬件", "画", "ai/chatgpt", "食", "3d", "漫",
    "编程/硬件/DIY", "摄影", "思考", "纪录片",
    "COC/DND/跑团/桌游", "Linux", "Blender", "大佬",
    "Sherlock Holmes", "学习", "手办", "番", "鬼畜", "Hp",
    "魂系", "音乐", "生化", "Lovecraft", "搞笑", "武/健",
    "权游", "篮球", "妹汁", "火影", "漫威/DC", "技", "游戏",
    "电影", "AC",
}

# ── Keyword rules (same as before) ─────────────────────────
UPLOADER_RULES = {
    "央视新闻": "时事",
    "观察者网": "观察",
    "志新观天下": "观察",
    "杰哥观察者模式启动中": "观察",
    "东方今报": "时事",
    "远康夫妇": "观察",
    "STN工作室": "游戏",
    "光影笔墨": "画",
    "独夫之心": "思考",
}

KEYWORD_RULES = [
    (r"(?i)\b(python|rust|golang|java|react|vue|docker|kubernetes|git)\b", "编程/硬件/DIY", "高"),
    (r"(?i)\b(llm|transformer|fine.?tun|rag|langchain|embedding)\b", "ai/chatgpt", "高"),
    (r"(?i)\b(stable.?diffusion|midjourney|comfyui|flux)\b", "ai/chatgpt", "高"),
    (r"(?i)\b(deepseek|deep.?seek)\b", "DeepSeek", "高"),
    (r"(?i)\b(claude|gpt|chatgpt|openai|gemini|llama)\b", "ai/chatgpt", "高"),
    (r"(?i)\b(ai.?agent|multi.?agent|agent.?框架|mcp)\b", "ai agent", "高"),
    (r"(?i)\b(鸿蒙|harmonyos|openharmony)\b", "鸿蒙", "高"),
    (r"(?i)\b(linux|ubuntu|debian|arch|fedora)\b", "Linux", "高"),
    (r"(?i)\b(blender|3d.?建模|三维|渲染)\b", "Blender", "高"),
    (r"(?i)\b(嵌入式|单片机|stm32|arduino|esp32|树莓派)\b", "嵌入式", "高"),
    (r"(?i)\b(量子|qubit|quantum|超导)\b", "量子", "高"),
    (r"(?i)\b(amd|radeon|ryzen|nvidia|cuda)\b", "硬件", "高"),
    (r"(?i)\b(fpv|穿越机|航模|无人机)\b", "fpv", "高"),
    (r"(?i)\b(航天|火箭|spacex|卫星|空间站|登月|火星)\b", "航空航天", "高"),
    (r"(?i)\b(机器人|robotic|机械臂|波士顿动力|unitree)\b", "机器人", "高"),
    (r"(?i)\b(新能源|电动汽?车|tesla|特斯拉|比亚迪|蔚来|小鹏)\b", "新能源汽车", "高"),
    (r"(?i)\b(低空|evtol|飞行汽车|城市空中交通)\b", "低空", "高"),
    (r"(?i)\b(军事|航母|战斗机|导弹|坦克|潜艇|海军|空军|国防)\b", "军", "高"),
    (r"(?i)\b(美国|川普|特朗普|拜登|白宫|关税|贸易战)\b", "美国", "高"),
    (r"(?i)\b(时事|新闻|国际|外交|联合国|北约|制裁)\b", "时事", "高"),
    (r"(?i)\b(申遗|非遗|文化遗产|传统.*手艺)\b", "申遗", "高"),
    (r"(?i)\b(黑神话|悟空|黑风山|黄风岭)\b", "黑神话", "高"),
    (r"(?i)\b(魂系|黑暗之魂|血源|只狼|法环|艾尔登法环)\b", "魂系", "高"),
    (r"(?i)\b(游戏|实况|攻略|通关|速通|boss)\b", "游戏", "高"),
    (r"(?i)\b(mod|mod制?作|改模|模组)\b", "Mod", "高"),
    (r"(?i)\b(三国|曹操|刘备|孙权|诸葛亮|三国志)\b", "三国", "高"),
    (r"(?i)\b(桌游|跑团|coc|dnd|trpg|克苏鲁的呼唤)\b", "COC/DND/跑团/桌游", "高"),
    (r"(?i)\b(火影|naruto|鸣人|佐助|卡卡西)\b", "火影", "高"),
    (r"(?i)\b(奥特曼|ultraman|迪迦|赛罗|泽塔)\b", "奥特曼", "高"),
    (r"(?i)\b(漫威|dc|marvel|钢铁侠|蜘蛛侠|蝙蝠侠|复仇者)\b", "漫威/DC", "高"),
    (r"(?i)\b(权游|权力的游戏|冰与火之歌|龙妈)\b", "权游", "高"),
    (r"(?i)\b(生化危机|resident.?evil|丧尸|僵尸|保护伞)\b", "生化", "高"),
    (r"(?i)\b(lovecraft|克苏鲁|拉莱耶|旧日支配)\b", "Lovecraft", "高"),
    (r"(?i)\b(sherlock|福尔摩斯|华生|贝克街)\b", "Sherlock Holmes", "高"),
    (r"(?i)\b(hp|harry.?potter|哈利.?波特|霍格沃茨)\b", "Hp", "高"),
    (r"(?i)\b(番剧|新番|动漫|anime|追番)\b", "番", "高"),
    (r"(?i)\b(漫画|漫改|漫剪|漫画解说)\b", "漫", "高"),
    (r"(?i)\b(鬼畜|恶搞|吐槽|调音|人力vocaloid)\b", "鬼畜", "高"),
    (r"(?i)\b(手办|gk|雕像|模玩|figma)\b", "手办", "高"),
    (r"(?i)\b(vam|virt.?a.?mate)\b", "vam", "高"),
    (r"(?i)\b(电影|影评|预告片|trailer|微电影)\b", "电影", "高"),
    (r"(?i)\b(纪录片|documentary|纪实|探索|bbc|国家地理)\b", "纪录片", "高"),
    (r"(?i)\b(音乐|mv|翻唱|remix|电音|说唱|hip.?hop|钢琴|吉他)\b", "音乐", "高"),
    (r"(?i)\b(搞笑|沙雕|爆笑|笑死|整活|社死)\b", "搞笑", "高"),
    (r"(?i)\b(篮球|nba|科比|詹姆斯|库里|杜兰特)\b", "篮球", "高"),
    (r"(?i)\b(骑行|自行车|公路车|山地车)\b", "骑行", "高"),
    (r"(?i)\b(美食|做饭|料理|菜谱|烹饪|烘焙|探店|吃播)\b", "食", "高"),
    (r"(?i)\b(穿搭|时尚|ootd|潮流|发型|美妆|化妆)\b", "妹汁", "低"),
    (r"(?i)\b(健身|哑铃|俯卧撑|引体|马拉松|跑步|瑜伽)\b", "武/健", "高"),
    (r"(?i)\b(武术|格斗|拳击|ufc|mma|散打|太极)\b", "武/健", "高"),
    (r"(?i)\b(摄影|拍照|相机|镜头|sony|佳能|尼康|富士|街拍)\b", "摄影", "高"),
    (r"(?i)\b(绘画|油画|素描|水彩|速写|板绘|procreate)\b", "画", "高"),
    (r"(?i)\b(设计|ui|ux|figma|sketch|photoshop|排版|字体)\b", "画", "高"),
    (r"(?i)\b(高数|微积分|线性代数|概率论|数学分析)\b", "高数", "高"),
    (r"(?i)\b(教程|教学|入门|速成|手把手|保姆级)\b", "学习", "高"),
    (r"(?i)\b(ppt|powerpoint|幻灯片|演示)\b", "ppt", "高"),
    (r"(?i)\b(科幻|赛博|cyberpunk|未来|外星|宇宙|星际)\b", "科幻", "高"),
    (r"(?i)\b(思考|哲学|人生|哲理|认知|思辨)\b", "思考", "高"),
    (r"(?i)\b(妹[子汁]|美女|小姐姐|jk|黑丝|福利)\b", "妹汁", "高"),
    (r"(?i)\b(acg|二次元|cosplay|漫展|同人|vocaloid|初音)\b", "AC", "高"),
]

def load_videos():
    with open(VIDEOS_FILE) as f:
        return json.load(f)

def load_enriched():
    if os.path.exists(ENRICHED_FILE):
        with open(ENRICHED_FILE) as f:
            return json.load(f)
    return {}

def load_existing_llm():
    if os.path.exists(EXISTING_LLM):
        with open(EXISTING_LLM) as f:
            return json.load(f)
    return {}

def preprocess():
    videos = load_videos()
    enriched = load_enriched()
    existing = load_existing_llm()

    print(f"📦 总视频: {len(videos)}")
    print(f"🏷️  有分区数据: {len(enriched)}")
    print(f"🤖 已有 LLM 分类: {len(existing)}")

    results = {}
    stats = defaultdict(lambda: {"count": 0, "高": 0, "中": 0, "低": 0, "分区": 0, "关键词": 0, "UP主": 0})

    for v in videos:
        bvid = v["bvid"]
        if bvid in existing:
            continue

        title = v["title"]
        upper = v.get("upper", "")
        matched = None
        method = ""

        # 1. Partition-based classification (highest confidence)
        if bvid in enriched and enriched[bvid].get("tid"):
            tid = enriched[bvid]["tid"]
            folder, conf = get_folder_for_tid(tid, title, upper)
            if folder and folder in VALID_FOLDERS:
                matched = (folder, conf or "高")
                method = "分区"

        # 2. Uploader rules
        if not matched and upper in UPLOADER_RULES:
            folder = UPLOADER_RULES[upper]
            if folder in VALID_FOLDERS:
                matched = (folder, "高")
                method = "UP主"

        # 3. Keyword rules
        if not matched:
            text = f"{title} {v.get('intro', '')} {upper}"
            for pattern, folder, confidence in KEYWORD_RULES:
                if re.search(pattern, text):
                    if folder in VALID_FOLDERS:
                        matched = (folder, confidence)
                        method = "关键词"
                        break

        if matched:
            folder, confidence = matched
            results[bvid] = {
                "title": title,
                "upper": upper,
                "suggested_folder": folder,
                "confidence": confidence,
                "method": method,
            }
            stats[folder]["count"] += 1
            stats[folder][confidence] = stats[folder].get(confidence, 0) + 1
            if method == "分区":
                stats[folder]["分区"] = stats[folder].get("分区", 0) + 1
            elif method == "关键词":
                stats[folder]["关键词"] = stats[folder].get("关键词", 0) + 1
            elif method == "UP主":
                stats[folder]["UP主"] = stats[folder].get("UP主", 0) + 1

    # Merge with existing LLM
    merged = dict(existing)
    merged.update(results)

    # Save
    with open(PREPROCESS_OUT, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    left = [v for v in videos if v["bvid"] not in merged]
    with open(LEFT_FOR_LLM, "w") as f:
        json.dump(left, f, ensure_ascii=False, indent=2)

    # ── Report ──
    total_classified = len(merged)
    by_method = Counter(r.get("method", "LLM") for r in results.values())
    by_method["LLM已有"] = len(existing)

    print(f"\n{'='*60}")
    print(f"📊 预处理结果:")
    print(f"  LLM 已有:     {len(existing):>5} 个")
    for m, c in sorted(by_method.items(), key=lambda x: -x[1]):
        if m != "LLM已有":
            print(f"  {m:12s}  {c:>5} 个")
    print(f"  ─────────────────────")
    print(f"  已分类合计:   {total_classified:>5} 个 ({total_classified/len(videos)*100:.1f}%)")
    print(f"  剩余给 LLM:   {len(left):>5} 个 ({len(left)/len(videos)*100:.1f}%)")

    # Confidence
    local_high = sum(1 for r in results.values() if r["confidence"] == "高")
    local_mid = sum(1 for r in results.values() if r["confidence"] == "中")
    local_low = sum(1 for r in results.values() if r["confidence"] == "低")
    print(f"\n  本地预处理置信度: 高={local_high} 中={local_mid} 低={local_low}")

    # Top folders
    print(f"\n  TOP 15 本地分配文件夹:")
    for folder, s in sorted(stats.items(), key=lambda x: -x[1]["count"])[:15]:
        print(f"    {folder:20s} → {s['count']:>4} 个 "
              f"(高:{s.get('高',0)} 中:{s.get('中',0)} 低:{s.get('低',0)}) "
              f"[分区:{s.get('分区',0)} 关键词:{s.get('关键词',0)} UP主:{s.get('UP主',0)}]")

    # Left-for-LLM stats
    if left:
        left_tids = Counter()
        for v in left:
            if v["bvid"] in enriched and enriched[v["bvid"]].get("tid"):
                left_tids[enriched[v["bvid"]]["tid"]] += 1

        print(f"\n📋 留给 LLM 的 {len(left)} 个:")
        if left_tids:
            print(f"  TOP 分区 (未匹配到文件夹):")
            for tid, cnt in left_tids.most_common(10):
                tname = TID_MAP.get(tid, ("?", "?"))[0]
                print(f"    tid={tid} ({tname}): {cnt} 个")

    return results, left, existing

if __name__ == "__main__":
    preprocess()
