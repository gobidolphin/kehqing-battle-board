#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
客情作战台 feed.json 云端每日更新脚本（GitHub Actions 用，仅标准库）

逻辑：
1. 今天（北京时间）已更新过且新闻 >=4 条 → 跳过（保留本机 WorkBuddy 自动化产出的精编内容）
2. 从量子位 / 机器之心 / 36氪 RSS 抓取 AI 新闻，按关键词分国内/国外，凑 2+2（不足则互补）
3. 知识点从内置销售知识池按一年中的第几天轮换（不依赖外网，保证每天有货）
4. 抓取严重失败（<2 条）→ 不写文件直接退出，明天再试
"""
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

BJT = timezone(timedelta(hours=8))
NOW = datetime.now(BJT)
TODAY = NOW.strftime("%Y-%m-%d")
TODAY_MD = NOW.strftime("%m/%d")

FEED_PATH = "feed.json"

FEEDS = [
    ("量子位", "https://www.qbitai.com/feed"),
    ("机器之心", "https://www.jiqizhixin.com/rss"),
    ("36氪", "https://36kr.com/feed"),
]

AI_KEYWORDS = re.compile(
    r"AI|大模型|模型|智能|GPT|Claude|Gemini|Agent|算力|机器人|自动驾驶|"
    r"OpenAI|Anthropic|DeepSeek|混元|千问|豆包|推理|训练|token|Token|GPU|芯片",
    re.I,
)
US_KEYWORDS = re.compile(
    r"OpenAI|GPT|Anthropic|Claude|谷歌|Google|Gemini|微软|Microsoft|Meta|"
    r"英伟达|NVIDIA|xAI|马斯克|硅谷|美国|苹果|Apple|亚马逊|Amazon|Mistral|"
    r"Figure|特斯拉|Tesla|Oracle|甲骨文|AMD|英特尔|Intel",
    re.I,
)

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean(text, limit=70):
    if not text:
        return ""
    t = TAG_RE.sub("", text)
    t = WS_RE.sub(" ", t).strip()
    t = t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">") \
         .replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    if len(t) > limit:
        t = t[:limit].rstrip("，。、； ") + "…"
    return t


def parse_date(raw):
    """解析 RSS pubDate / Atom updated，返回北京时间 MM/DD。"""
    if not raw:
        return TODAY_MD
    raw = raw.strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%SZ", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(BJT).strftime("%m/%d")
        except ValueError:
            continue
    return TODAY_MD


def regex_fallback_items(data):
    """RSS/Atom XML 解析失败时的正则兜底：提取 <item> 块。"""
    text = data.decode("utf-8", errors="ignore")
    items = []

    def pick(block, tag):
        m = re.search(
            r"<" + tag + r"[^>]*>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</" + tag + r">",
            block, re.S)
        return m.group(1).strip() if m else ""

    for block in re.findall(r"<item\b.*?</item>", text, re.S)[:25]:
        link = pick(block, "link")
        if not link:
            m = re.search(r'<link[^>]*href="([^"]+)"', block)
            link = m.group(1) if m else ""
        items.append((pick(block, "title"), link,
                      pick(block, "description"), pick(block, "pubDate")))
    return items


def fetch_feed(name, url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read()
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        # 部分站点 RSS 存在非法字符，退化为正则提取
        return regex_fallback_items(data)
    items = []
    # RSS 2.0
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        desc = it.findtext("description") or ""
        date = it.findtext("pubDate") or it.findtext(
            "{http://purl.org/dc/elements/1.1/}date") or ""
        items.append((title, link, desc, date))
    # Atom
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for it in root.findall("a:entry", ns):
        title = (it.findtext("a:title", default="", namespaces=ns) or "").strip()
        link_el = it.find("a:link", ns)
        link = link_el.get("href", "") if link_el is not None else ""
        desc = it.findtext("a:summary", default="", namespaces=ns) or \
            it.findtext("a:content", default="", namespaces=ns) or ""
        date = it.findtext("a:published", default="", namespaces=ns) or \
            it.findtext("a:updated", default="", namespaces=ns) or ""
        items.append((title, link, desc, date))
    return items


def collect_news():
    seen = set()
    us, cn = [], []
    for name, url in FEEDS:
        try:
            items = fetch_feed(name, url)
        except Exception as e:
            print(f"[warn] {name} 抓取失败: {e}", file=sys.stderr)
            continue
        for title, link, desc, date in items[:25]:
            title = clean(title, 45)
            if not title or len(title) < 8:
                continue
            if name == "36氪" and not AI_KEYWORDS.search(title):
                continue
            key = re.sub(r"[\W_]+", "", title)[:20]
            if key in seen:
                continue
            seen.add(key)
            item = {
                "region": "us" if US_KEYWORDS.search(title) else "cn",
                "title": title,
                "body": clean(desc, 70) or title,
                "link": link,
                "time": parse_date(date),
            }
            (us if item["region"] == "us" else cn).append(item)
    # 目标 2 国外 + 2 国内，不足互相补齐，共 4 条
    news = us[:2] + cn[:2]
    rest = us[2:] + cn[2:]
    while len(news) < 4 and rest:
        news.append(rest.pop(0))
    return news[:4]


# ---- 销售向知识点池（每日轮换，不依赖外网） ----
KNOWLEDGE_POOL = [
    {"title": "SPIN 提问法：先问处境，再问难点",
     "body": "大客户销售不是靠介绍产品开场，而是靠提问。SPIN 四步：背景问题摸现状、难点问题挖痛点、暗示问题放大代价、需求效益问题让客户自己说出价值。",
     "takeaway": "下次拜访前写 4 个问题，每类一个，让客户说够 70% 的时间。"},
    {"title": "谋攻之道：先算清采购地图再出手",
     "body": "B2B 采购是集体决策。动手前先画采购地图：谁拍板（EB）、谁把关（TB）、谁天天用（UB）、谁帮你说话（Coach）。缺一角，单子就可能卡在那一角。",
     "takeaway": "把你手上的重点客户逐个过一遍：四个角色各是谁？缺谁补谁。"},
    {"title": "Coach 不是朋友，是利益共同体",
     "body": "Coach（内线教练）帮你是因为你的赢单对他有利——政绩、省事、露脸。判断真伪 Coach 的标准：他是否给你独家信息、是否帮你安排关键会面。",
     "takeaway": "给每个重点商机找一名 Coach，并用'独家信息'和'牵线动作'两条标准检验成色。"},
    {"title": "影响力六原则之互惠：先给，再要",
     "body": "人对'欠人情'极度敏感。销售中的互惠不是送礼，而是先提供有价值的帮助：一份行业报告、一次免费诊断、一个有用的引荐。",
     "takeaway": "本周给目标客户各送一份'与他业务相关'的资料，只给不求，下周再约。"},
    {"title": "影响力六原则之社会认同：同行案例是最硬的通货",
     "body": "客户在不确定时会看别人怎么做。同规模、同行业、同场景的成功案例，比十页产品 PPT 更有说服力。",
     "takeaway": "整理 3 个与你目标客户同行业的落地案例，做成一页纸，拜访时先讲案例再讲产品。"},
    {"title": "被拒绝是信号，不是终点",
     "body": "客户说'不需要'往往意味着：没听懂价值、时机不对、或找错了人。拒绝里藏着信息——问一句'主要卡在哪'，比换下一个客户更值钱。",
     "takeaway": "复盘最近 3 次被拒：分别卡在价值、时机还是人？各写一条下次的应对话术。"},
    {"title": "电话优先于微信",
     "body": "冷启动阶段电话的触达效率远高于微信：电话 1 分钟能问清体量，微信可能三天不回。加上微信是结果，不是起点。",
     "takeaway": "今天的客户触达，先打电话；通话超过 1 分钟再加微信沉淀。"},
    {"title": "1 分钟内问出 IT 体量",
     "body": "陌生电话的黄金 60 秒：自我介绍 15 秒、夸客户 15 秒、一个体量问题 30 秒。'咱们 IT 团队大概多少人/月 IT 支出什么量级'，答了就是有效建联。",
     "takeaway": "把体量问题背成肌肉记忆，每通电话必问，记进作战台。"},
    {"title": "关键词破冰：用客户的语言开场",
     "body": "陌拜/冷电话开场不要背公司介绍，要用客户行业的关键词：银行聊风控与信创、SaaS 聊获客成本、制造聊供应链。关键词对了，门就开了。",
     "takeaway": "给明天要拜访的客户写 3 个他行业的关键词，编进开场 30 秒。"},
    {"title": "三段式话术：介绍—夸客户—给方案",
     "body": "陌拜标准结构：15 秒介绍我是谁，15 秒夸客户（最近的动作/新闻，证明你做过功课），30 秒给方案（我能帮你解决什么具体问题）。",
     "takeaway": "夸客户的部分必须具体到一条新闻或一个产品动作，空泛的夸奖等于没夸。"},
    {"title": "外卖理论：判断客户活得怎么样",
     "body": "判断一家公司经营状况的土办法：看招聘（BOSS 直聘在招岗位数）、看外卖（楼下骑手密度）、看加班灯。三个信号比财报更及时。",
     "takeaway": "陌拜前花 2 分钟查客户在招岗位数，写进卡片'业务描述'。"},
    {"title": "客户分层：把时间花在 P0 上",
     "body": "P0 每天触达、P1 隔天、P2 每周、P3 暂停。分层不是给客户贴标签，是给自己的时间定价——80% 的成交来自 20% 的客户。",
     "takeaway": "打开作战台检查：P0 客户今天是否都已触达？没触达的现在就补。"},
    {"title": "商机要挂金额，不挂金额的都是线索",
     "body": "商机三要素：明确需求、预算量级、决策时间表。三者缺一，就只是线索。作战台里 deal>0 的客户必须能说出这三个数。",
     "takeaway": "检查作战台商机客户：谁能说清预算和时间表？说不清的下次拜访必须问。"},
    {"title": "暗示问题：把痛点换算成钱",
     "body": "客户不为'痛点'买单，为'代价'买单。把问题量化：一次资损多少、误拦率 1% 影响多少交易、人工审核一天多少钱。数字自己会说服人。",
     "takeaway": "为重点客户算一笔账：他的痛点一年值多少钱？写进方案第一页。"},
    {"title": "需求效益问题：让客户自己说服自己",
     "body": "SPIN 最后一步是问'如果解决了这个问题，对您有什么帮助'。客户自己说出的价值，比你讲的可信十倍。",
     "takeaway": "下次演示结束前问一句：'如果这个能力上线，您团队最先用在哪？'"},
    {"title": "承诺与一致：小答应带来大答应",
     "body": "人倾向于和自己之前的承诺保持一致。销售推进就是让客户不断做小承诺：先答应试用、再答应拉会、再答应引荐决策人。",
     "takeaway": "每次沟通结束前锁定一个'下一步小承诺'，并写进跟进记录。"},
    {"title": "权威原则：借背书，不吹自己",
     "body": "自己说自己好是广告，权威说你行才是背书。用好手头资源：标杆客户名单、POC 测试报告、合规认证、评测榜单。",
     "takeaway": "整理一页'背书清单'：标杆案例+认证+报告，放进每次方案的标准附件。"},
    {"title": "稀缺原则：给客户一个现在行动的理由",
     "body": "没有期限的优惠等于没有优惠。POC 名额、季度价格、政策窗口期——给客户一个'为什么是这周'的理由，单子才不会无限拖延。",
     "takeaway": "为停滞商机各设计一个时间锚点，下次沟通时明确提出。"},
    {"title": "拜访纪要不过夜",
     "body": "拜访结束 2 小时内写纪要，记忆留存率最高。纪要三要素：客户原话（需求/顾虑）、关键人态度、下一步动作+时间点。",
     "takeaway": "今天的拜访现在就补纪要，原话用引号记下来。"},
    {"title": "先讲损失，再讲收益",
     "body": "损失厌恶：人对损失的感受是收益的两倍多。'不用风控一年可能损失多少'比'用了能省多少'更有推动力。",
     "takeaway": "把方案第一页的'收益'改成'不做的代价'，对比测试客户反应。"},
    {"title": "TB 技术把关人：别让他成为你的暗礁",
     "body": "技术把关人（TB）没有购买权，但有否决权。他们关心稳定性、兼容性、合规。方案里提前回答这三个问题，暗礁变助攻。",
     "takeaway": "给在途商机列一下：对方 TB 是谁？他最可能用什么理由否决？预案是什么？"},
    {"title": "UB 使用者：口碑的源头",
     "body": "真正天天用产品的是 UB。UB 的一句'难用'能杀死续约，一句'离不开'能撬动扩容。交付后持续经营 UB 关系。",
     "takeaway": "给已交付客户的 UB 做一次使用回访，记录三条真实反馈。"},
    {"title": "EB 决策人：只谈价值，不谈功能",
     "body": "拍板的人不关心参数，关心结果：降多少成本、控多少风险、出什么政绩。对 EB 的汇报一页封顶，只讲钱和风险。",
     "takeaway": "把方案里所有功能描述翻译成'省钱/控险/政绩'三类价值语言。"},
    {"title": "冷电话的 KPI 是'下一个动作'，不是成交",
     "body": "陌拜电话的成功率本来就低，衡量标准不是成交，而是推进：问到体量、加上微信、约到拜访，三选一就算赢。",
     "takeaway": "今天每通电话结束问自己：拿到了三个动作里的哪一个？记进 ColdCall 记录。"},
    {"title": "复盘比努力更稀缺",
     "body": "打 50 通电话不复盘，不如打 20 通+逐条复盘。复盘三问：哪句话让客户愿意聊了？哪句话把天聊死了？下次改哪个字？",
     "takeaway": "本周挑 5 通电话逐句复盘，沉淀 2 条可复用话术进 SOP。"},
    {"title": "方案的第一页写给客户，不是写给自己",
     "body": "90% 的方案第一页在讲'我们公司多牛'。客户只关心'你能帮我解决什么'。第一页应该是：你的问题+我的解法+量化收益。",
     "takeaway": "打开最近在用的方案模板，把第一页改成客户问题开场。"},
    {"title": "跟进的本质是提供新信息",
     "body": "'在吗''考虑得怎么样'是最差的跟进。好的跟进每次都带新信息：一条相关政策、一个同行案例、一项新功能，给客户回你的理由。",
     "takeaway": "给停滞超 7 天的商机各找一条'新信息'，今天就发出去。"},
    {"title": "客户分层降级也是纪律",
     "body": "连续 3 次触达无实质进展的客户，果断从 P0 降到 P1 甚至 P2。不是放弃，是把今天的时间让给更热的商机。",
     "takeaway": "检查作战台 P0 名单：谁连续两周没进展？今天降级，把时间腾出来。"},
    {"title": "POC 的胜负在测试标准，不在技术",
     "body": "POC 启动前先和客户定死验收标准：测什么数据、什么指标、多少算通过。标准不清的 POC，测完只会换来一句'再看看'。",
     "takeaway": "在手 POC 项目：验收标准是否白纸黑字？没有的话本周补上。"},
    {"title": "报价是价值谈判，不是价格让步",
     "body": "客户压价时，先确认价值是否讲透，再谈价格。降价必须换东西：换更长合约、换案例授权、换首付款比例。",
     "takeaway": "准备一张'让步交换表'：每档折扣对应要什么交换条件。"},
]


def rotate_knowledge():
    idx = NOW.timetuple().tm_yday % len(KNOWLEDGE_POOL)
    return KNOWLEDGE_POOL[idx]


def main():
    try:
        with open(FEED_PATH, "r", encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        old = {}

    # 今天已更新且新闻够数 → 保留（可能是本机自动化的精编内容）
    if old.get("date") == TODAY and len(old.get("news", [])) >= 4:
        print(f"[skip] feed.json 已是今天({TODAY})的内容，不覆盖")
        return

    news = collect_news()
    if len(news) < 2:
        print(f"[fail] 有效新闻不足({len(news)}条)，本次不更新", file=sys.stderr)
        sys.exit(1)

    feed = {
        "date": TODAY,
        "news": news,
        "knowledge": rotate_knowledge(),
        "_ts": int(NOW.timestamp() * 1000),
    }
    with open(FEED_PATH, "w", encoding="utf-8") as f:
        json.dump(feed, f, ensure_ascii=False, indent=2)
    print(f"[ok] feed.json 更新为 {TODAY}，新闻 {len(news)} 条：")
    for n in news:
        print(f"  - [{n['region']}] {n['title']}")


if __name__ == "__main__":
    main()
