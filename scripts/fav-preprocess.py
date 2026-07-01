#!/usr/bin/env python3
"""
fav-preprocess.py — 本地预处理：规则匹配 + UP主聚类 + 启发式分类
================================================================
把 7279 个视频中能本地确定的部分先分类，剩下的才送 DeepSeek。
"""

import json, os, re, sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(ROOT, "..", "cache")
VIDEOS_FILE = os.path.join(CACHE_DIR, "fav-videos.json")
PREPROCESS_OUT = os.path.join(CACHE_DIR, "fav-preprocess.json")  # 预处理结果
LEFT_FOR_LLM = os.path.join(CACHE_DIR, "fav-left-for-llm.json")   # 剩下给LLM的
EXISTING_LLM = os.path.join(CACHE_DIR, "fav-classify.json")        # 已有的LLM结果

# ── Folder list (from API, frozen here) ─────────────────────
FOLDERS = {
    "下载": 4106246717,
    "牢啊": 3794540917,
    "量子": 3770619717,
    "低空": 3730161517,
    "鸿蒙玩法": 3712372717,
    "稍后": 3652081117,
    "时事": 3655056717,
    "骑行": 3675559617,
    "问题_科技": 3737175017,
    "科技创新": 3675540817,
    "美国": 3654805517,
    "新能源汽车": 3684253717,
    "93": 3662946217,
    "观察": 3712909717,
    "ai agent": 3674001217,
    "三国": 3605623217,
    "Mod": 3459098117,
    "DeepSeek": 3430493117,
    "机器人": 3496888917,
    "航空航天": 3406428217,
    "申遗": 3382321017,
    "军": 3355399817,
    "fpv": 3378575517,
    "奥特曼": 3352704517,
    "ppt": 3395872517,
    "amd": 3368818917,
    "高数": 3316286017,
    "黑神话": 3350870517,
    "vam": 3305746117,
    "科幻": 3100567617,
    "嵌入式": 3155780717,
    "软件": 3071489117,
    "网络": 3077080817,  # 注意有2个"网络"文件夹, 这个是id较小的
    "编程": 3115845717,
    "网络(2)": 2957481817,  # 第二个"网络"文件夹
    "鸿蒙": 2921046217,
    "硬件": 2917908417,
    "画": 2539070417,
    "ai/chatgpt": 2247311817,
    "食": 2047613617,
    "3d": 1845851217,
    "漫": 1699358217,
    "编程/硬件/DIY": 1146355217,
    "摄影": 295393517,
    "思考": 215483417,
    "纪录片": 200596117,
    "COC/DND/跑团/桌游": 189699717,
    "Linux": 49888217,
    "Blender": 124765917,
    "大佬": 47464017,
    "Sherlock Holmes": 120137417,
    "学习": 156726317,
    "手办": 156482817,
    "番": 155754917,
    "鬼畜": 60611117,
    "Hp": 154338617,
    "魂系": 145617917,
    "音乐": 133110117,
    "生化": 129520117,
    "Lovecraft": 129170217,
    "搞笑": 127493817,
    "武/健": 121809417,
    "权游": 118020417,
    "篮球": 115792117,
    "妹汁": 112121517,
    "火影": 112121017,
    "漫威/DC": 112046717,
    "技": 111641717,
    "游戏": 111506717,
    "电影": 110833417,
    "AC": 107468017,
}

# ── Rule definitions ────────────────────────────────────────

# 1. Uploader → folder mappings (from known data + reasonable inference)
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
    "账号已注销": None,  # 已注销的UP主太多，不放规则
}

# 2. Keyword → folder rules (ordered by priority, first match wins)
# Format: (regex_pattern, folder_name, min_confidence)
KEYWORD_RULES = [
    # Technology / Programming
    (r"(?i)\b(python|rust|golang|java|typescript|react|vue|docker|kubernetes|git|vscode)\b", "编程/硬件/DIY", "高"),
    (r"(?i)\b(llm|transformer|attention|fine.?tun|rag|langchain|embedding|向量数据库)\b", "ai/chatgpt", "高"),
    (r"(?i)\b(whisper|stt|tts|语音合成|语音识别|asr)\b", "ai/chatgpt", "高"),
    (r"(?i)\b(stable.?diffusion|midjourney|comfyui|flux|sd\d\.?\d?)\b", "ai/chatgpt", "高"),
    (r"(?i)\b(deepseek|deep.?seek)\b", "DeepSeek", "高"),
    (r"(?i)\b(claude|gpt-?4|chatgpt|openai|gemini|llama|mistral)\b", "ai/chatgpt", "高"),
    (r"(?i)\b(ai.?agent|multi.?agent|agent.?框架|agent.?协作|mcp)\b", "ai agent", "高"),
    (r"(?i)\b(鸿蒙|harmonyos|openharmony)\b", "鸿蒙", "高"),
    (r"(?i)\b(鸿蒙).*(玩法|教程|开发|入门)\b", "鸿蒙玩法", "高"),
    (r"(?i)\b(linux|ubuntu|debian|arch|fedora|centos)\b", "Linux", "高"),
    (r"(?i)\b(blender|3d.?建模|三维|渲染|cycles|eevee)\b", "Blender", "高"),
    (r"(?i)\b(嵌入式|单片机|stm32|arduino|esp32|raspberry.?pi|树莓派)\b", "嵌入式", "高"),
    (r"(?i)\b(量子|qubit|quantum|超导|量子计算机)\b", "量子", "高"),
    (r"(?i)\b(amd|radeon|ryzen|nvidia|cuda)\b", "硬件", "高"),
    (r"(?i)\b(amd)\b", "amd", "高"),
    (r"(?i)\b(fpv|穿越机|航模|无人机.*飞)\b", "fpv", "高"),
    (r"(?i)\b(ug|solidworks|fusion.?360|autocad|inventor)\b", "3d", "高"),

    # Science / Engineering
    (r"(?i)\b(航天|火箭|spacex|卫星|空间站|登月|火星)\b", "航空航天", "高"),
    (r"(?i)\b(机器人|robotic|机械臂|波士顿动力|figure.?ai|unitree)\b", "机器人", "高"),
    (r"(?i)\b(新能源|电动汽?车|电池|tesla|特斯拉|比亚迪|蔚来|小鹏|理想汽车)\b", "新能源汽车", "高"),
    (r"(?i)\b(低空|evtol|飞行汽车|城市空中交通)\b", "低空", "高"),
    (r"(?i)\b(科技创新|量子计算|核聚变|超导|脑机|基因编辑|crispr)\b", "科技创新", "中"),

    # Military / Politics
    (r"(?i)\b(军事|航母|战斗机|导弹|坦克|潜艇|海军|空军|陆军|国防)\b", "军", "高"),
    (r"(?i)\b(战士|战斗|战争|战场|特种兵|海军陆战队)\b", "军", "中"),
    (r"(?i)\b(美国|川普|特朗普|拜登|白宫|五角大楼|关税|贸易战)\b", "美国", "高"),
    (r"(?i)\b(时事|新闻|国际|外交|联合国|北约|制裁|协议|谈判)\b", "时事", "高"),
    (r"(?i)\b(申遗|非遗|文化遗产|传统.*手艺|非遗.*传承)\b", "申遗", "高"),

    # Gaming
    (r"(?i)\b(黑神话|悟空|黑风山|黄风岭)\b", "黑神话", "高"),
    (r"(?i)\b(魂系|黑暗之魂|血源|只狼|法环|艾尔登法环|elden.?ring)\b", "魂系", "高"),
    (r"(?i)\b(游戏|实况|攻略|通关|速通|boss|全收集)\b", "游戏", "高"),
    (r"(?i)\b(mod|modder|mod制?作|改模|模组)\b", "Mod", "高"),
    (r"(?i)\b(三国|曹操|刘备|孙权|诸葛亮|三国志|三国杀|全战三国)\b", "三国", "高"),
    (r"(?i)\b(桌游|跑团|coc|dnd|d&d|trpg|克苏鲁的呼唤|龙与地下城)\b", "COC/DND/跑团/桌游", "高"),
    (r"(?i)\b(火影|naruto|鸣人|佐助|卡卡西|晓组织)\b", "火影", "高"),
    (r"(?i)\b(奥特曼|ultraman|迪迦|赛罗|泽塔)\b", "奥特曼", "高"),
    (r"(?i)\b(漫威|dc|marvel|钢铁侠|蜘蛛侠|蝙蝠侠|超人|复仇者)\b", "漫威/DC", "高"),
    (r"(?i)\b(权游|权力的游戏|冰与火之歌|龙妈|琼恩.*雪诺)\b", "权游", "高"),
    (r"(?i)\b(生化危机|resident.?evil|丧尸|僵尸|保护伞|浣熊市)\b", "生化", "高"),
    (r"(?i)\b(lovecraft|克苏鲁|拉莱耶|旧日支配|深潜者|印斯茅斯)\b", "Lovecraft", "高"),
    (r"(?i)\b(sherlock|福尔摩斯|华生|贝克街|神探夏洛克)\b", "Sherlock Holmes", "高"),
    (r"(?i)\b(hp|harry.?potter|哈利.?波特|霍格沃茨|魔法石|伏地魔)\b", "Hp", "高"),

    # Anime / Comics
    (r"(?i)\b(番剧|新番|动漫|动画|anime|追番|补番)\b", "番", "高"),
    (r"(?i)\b(漫画|漫改|漫剪|漫画解说)\b", "漫", "高"),
    (r"(?i)\b(鬼畜|恶搞|吐槽|调音|人力vocaloid)\b", "鬼畜", "高"),
    (r"(?i)\b(手办|gk|雕像|模玩|figma|黏土人)\b", "手办", "高"),
    (r"(?i)\b(vam|virt.?a.?mate)\b", "vam", "高"),

    # Entertainment
    (r"(?i)\b(电影|影评|预告片|trailer|短片|微电影)\b", "电影", "高"),
    (r"(?i)\b(纪录片|documentary|纪实|探索|bbc|国家地理|discovery)\b", "纪录片", "高"),
    (r"(?i)\b(音乐|mv|翻唱|remix|电音|说唱|hip.?hop|钢琴|吉他)\b", "音乐", "高"),
    (r"(?i)\b(搞笑|沙雕|爆笑|笑死|整活|社死)\b", "搞笑", "高"),
    (r"(?i)\b(篮球|nba|科比|詹姆斯|库里|杜兰特|cba)\b", "篮球", "高"),
    (r"(?i)\b(骑行|自行车|公路车|山地车|骑行台)\b", "骑行", "高"),

    # Lifestyle
    (r"(?i)\b(美食|做饭|料理|菜谱|烹饪|烘焙|探店|吃播)\b", "食", "高"),
    (r"(?i)\b(穿搭|时尚|ootd|潮流|发型|美妆|化妆)\b", "妹汁", "低"),  # 可能是妹汁也可能是其他
    (r"(?i)\b(健身|哑铃|俯卧撑|引体|马拉松|跑步|瑜伽)\b", "武/健", "高"),
    (r"(?i)\b(武术|格斗|拳击|ufc|mma|散打|太极)\b", "武/健", "高"),

    # Photography / Art
    (r"(?i)\b(摄影|拍照|相机|镜头|sony|佳能|尼康|富士|街拍|人像)\b", "摄影", "高"),
    (r"(?i)\b(绘画|油画|素描|水彩|速写|板绘|procreate)\b", "画", "高"),
    (r"(?i)\b(设计|ui|ux|figma|sketch|photoshop|排版|字体)\b", "画", "高"),

    # Education
    (r"(?i)\b(高数|微积分|线性代数|概率论|数学分析|复变)\b", "高数", "高"),
    (r"(?i)\b(教程|教学|入门|速成|手把手|保姆级|从零开始)\b", "学习", "高"),
    (r"(?i)\b(ppt|powerpoint|幻灯片|演示)\b", "ppt", "高"),

    # Other
    (r"(?i)\b(科幻|赛博|cyberpunk|未来|外星|宇宙|星际)\b", "科幻", "高"),
    (r"(?i)\b(思考|哲学|人生|哲理|认知|思辨|批判性)\b", "思考", "高"),
    (r"(?i)\b(妹[子汁]|美女|小姐姐|jk|黑丝|福利)\b", "妹汁", "高"),
    (r"(?i)\b(acg|二次元|cosplay|漫展|同人|vocaloid|初音)\b", "AC", "高"),
]

# 3. Duration-based heuristics
# Very short videos (<30s) are almost always 搞笑/鬼畜/番/memes
SHORT_THRESHOLD = 30  # seconds


def load_videos():
    with open(VIDEOS_FILE) as f:
        return json.load(f)


def load_existing_llm():
    """Load already-classified results from DeepSeek runs."""
    if os.path.exists(EXISTING_LLM):
        with open(EXISTING_LLM) as f:
            return json.load(f)
    return {}


def match_keyword_rules(video):
    """Try keyword rules against a video. Returns (folder, confidence) or None."""
    text = f"{video['title']} {video.get('intro', '')} {video.get('upper', '')}"

    for pattern, folder, confidence in KEYWORD_RULES:
        if re.search(pattern, text):
            # Verify folder exists
            if folder not in FOLDERS:
                continue
            return folder, confidence
    return None


def match_uploader_rules(video):
    """Try uploader rules. Returns (folder, confidence) or None."""
    upper = video.get('upper', '')
    if upper in UPLOADER_RULES:
        folder = UPLOADER_RULES[upper]
        if folder is None:
            return None  # explicitly excluded
        if folder in FOLDERS:
            return folder, "高"
    return None


def build_uploader_folder_map(classified, videos):
    """From classified videos, infer uploader→folder mapping.
    If an uploader has >=3 videos ALL going to the same folder, auto-classify all their videos."""
    from collections import defaultdict

    bvid_to_upper = {v["bvid"]: v.get("upper", "") for v in videos}
    uploader_folders = defaultdict(list)  # upper → list of folder names

    for bvid, info in classified.items():
        upper = bvid_to_upper.get(bvid, "")
        if upper and "suggested_folder" in info:
            uploader_folders[upper].append(info["suggested_folder"])

    mapping = {}
    for upper, folders in uploader_folders.items():
        if len(folders) >= 3:
            most_common = Counter(folders).most_common(1)[0]
            if most_common[1] >= len(folders) * 0.8:  # 80%+ consistency
                mapping[upper] = most_common[0]
    return mapping


def cluster_uploader_videos(videos, min_videos=6):
    """
    For uploaders with >=min_videos, cluster their videos by title similarity.
    Returns {upper: {folder_name: [bvid, ...]}}
    Only clusters that are strongly consistent get auto-assigned.
    """
    from difflib import SequenceMatcher

    # Group by uploader
    by_upper = defaultdict(list)
    for v in videos:
        upper = v.get("upper", "")
        if upper:
            by_upper[upper].append(v)

    # Only process uploaders with enough videos
    candidates = {u: vs for u, vs in by_upper.items() if len(vs) >= min_videos}

    clustered = {}  # upper → {folder → [bvid, ...]}

    for upper, vids in candidates.items():
        # Cluster by title similarity (simple approach: keyword extraction)
        # Count common title patterns
        title_words = defaultdict(int)
        for v in vids:
            # Extract meaningful words from title (2+ char Chinese words or 3+ char English)
            words = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', v["title"])
            for w in words:
                title_words[w.lower()] += 1

        # Find dominant keywords (appear in >=50% of this uploader's videos)
        threshold = max(2, len(vids) * 0.3)
        dominant_kw = [w for w, c in title_words.items() if c >= threshold]

        if not dominant_kw:
            continue

        # Try to match dominant keywords to folders
        for kw in dominant_kw:
            matched_folder = None
            for folder_name in FOLDERS:
                if kw.lower() in folder_name.lower() or folder_name.lower() in kw.lower():
                    matched_folder = folder_name
                    break
            if matched_folder:
                # Assign all videos with this keyword to the folder
                matched_bvids = []
                for v in vids:
                    if kw.lower() in v["title"].lower():
                        matched_bvids.append(v["bvid"])
                if len(matched_bvids) >= 2:
                    if upper not in clustered:
                        clustered[upper] = {}
                    clustered[upper][matched_folder] = matched_bvids

    return clustered


def preprocess():
    videos = load_videos()
    existing = load_existing_llm()

    print(f"📦 总视频: {len(videos)}")
    print(f"🤖 已有 LLM 分类: {len(existing)}")

    results = {}  # bvid → {suggested_folder, confidence, method, reason}
    stats = defaultdict(lambda: {"count": 0, "高": 0, "中": 0, "低": 0})

    # Phase 0: Cluster uploaders by title keywords
    print("🔍 UP主聚类分析...")
    uploader_clusters = cluster_uploader_videos(videos)
    total_clustered = sum(len(bvids) for u in uploader_clusters.values() for bvids in u.values())
    print(f"🔍 UP主聚类: {len(uploader_clusters)} 个UP主, 覆盖 {total_clustered} 个视频")

    # Phase 0b: Infer uploader→folder from classified data
    all_classified_so_far = dict(existing)
    inferred_uploader_map = build_uploader_folder_map(all_classified_so_far, videos)
    print(f"🔍 从已分类推断 UP主→文件夹: {len(inferred_uploader_map)} 个")

    for v in videos:
        bvid = v["bvid"]

        # Skip already classified by DeepSeek
        if bvid in existing:
            continue

        title = v["title"]
        upper = v.get("upper", "")
        duration = v.get("duration", 0)

        # Strategy: try rules in priority order
        matched = None
        method = ""

        # 0. Uploader clustering match
        if upper in uploader_clusters:
            for folder, bvids in uploader_clusters[upper].items():
                if bvid in bvids:
                    matched = (folder, "中")
                    method = "cluster"
                    break

        # 1. Hardcoded uploader rule
        if not matched:
            match = match_uploader_rules(v)
            if match:
                matched = match
                method = "uploader"

        # 2. Inferred uploader→folder (from classified data)
        if not matched and upper in inferred_uploader_map:
            folder = inferred_uploader_map[upper]
            if folder in FOLDERS:
                matched = (folder, "高")
                method = "uploader_inferred"

        # 3. Keyword rules
        if not matched:
            match = match_keyword_rules(v)
            if match:
                matched = match
                method = "keyword"

        if matched:
            folder, confidence = matched
            results[bvid] = {
                "title": title,
                "upper": upper,
                "suggested_folder": folder,
                "confidence": confidence,
                "method": method,
                "reason": f"{'UP主聚类' if method=='cluster' else 'UP主硬编码' if method=='uploader' else 'UP主推断' if method=='uploader_inferred' else '关键词'}: → {folder}",
            }
            stats[folder]["count"] += 1
            stats[folder][confidence] = stats[folder].get(confidence, 0) + 1

    # Merge with existing LLM results
    merged = dict(existing)
    merged.update(results)

    # Save
    with open(PREPROCESS_OUT, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # Left for LLM
    left = [v for v in videos if v["bvid"] not in merged]
    with open(LEFT_FOR_LLM, "w") as f:
        json.dump(left, f, ensure_ascii=False, indent=2)

    # ── Report ──
    total_classified = len(existing) + len(results)
    print(f"\n{'='*60}")
    print(f"📊 预处理结果:")
    print(f"  LLM 已有:     {len(existing):>5} 个")
    print(f"  本地预处理:   {len(results):>5} 个  (uploader: {sum(1 for r in results.values() if r.get('method')=='uploader')}, keyword: {sum(1 for r in results.values() if r.get('method')=='keyword')})")
    print(f"  ─────────────────────")
    print(f"  已分类合计:   {total_classified:>5} 个 ({total_classified/len(videos)*100:.1f}%)")
    print(f"  剩余给 LLM:   {len(left):>5} 个 ({len(left)/len(videos)*100:.1f}%)")

    # Confidence distribution of local preprocessing
    local_high = sum(1 for r in results.values() if r["confidence"] == "高")
    local_mid = sum(1 for r in results.values() if r["confidence"] == "中")
    local_low = sum(1 for r in results.values() if r["confidence"] == "低")
    print(f"\n  本地预处理置信度: 高={local_high} 中={local_mid} 低={local_low}")

    # Top folders from local preprocessing
    print(f"  TOP 10 本地分配文件夹:")
    for folder, s in sorted(stats.items(), key=lambda x: -x[1]["count"])[:10]:
        print(f"    {folder:20s} → {s['count']:>4} 个 (高:{s.get('高',0)} 中:{s.get('中',0)} 低:{s.get('低',0)})")

    # Left-for-LLM stats
    print(f"\n📋 留给 LLM 的 {len(left)} 个视频特征:")
    if left:
        left_uppers = Counter(v.get("upper", "") for v in left)
        print(f"  唯一 UP主: {len(left_uppers)}")
        print(f"  TOP 10 UP主:")
        for name, cnt in left_uppers.most_common(10):
            print(f"    {name}: {cnt} 个")
        left_duration_dist = {
            "<30s": sum(1 for v in left if v.get("duration", 0) < 30),
            "30s-1.5min": sum(1 for v in left if 30 <= v.get("duration", 0) < 90),
            "1.5-10min": sum(1 for v in left if 90 <= v.get("duration", 0) < 600),
            ">10min": sum(1 for v in left if v.get("duration", 0) >= 600),
        }
        print(f"  时长分布: {left_duration_dist}")

    return results, left, existing


if __name__ == "__main__":
    preprocess()
