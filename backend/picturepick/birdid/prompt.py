"""AI 识鸟提示词（docs/BIRDID-CONTRACT.md §4.2）。

提示词要求模型只输出 JSON；解析层（parser.py）仍做最大容错，
因此这里只负责「把期望格式说清楚」。

参考照片要求：**必须由大模型 API 提供真实存在的图片直链**（可结合方舟「联网搜索」工具
检索来源页面），不接受编造；找不到时留空字符串。
"""

from __future__ import annotations

SYSTEM_PROMPT = """你是资深鸟类识别专家。请根据照片识别画面中的鸟，并按下面的 JSON 结构输出。
如果你启用了联网搜索工具，请用它查找该鸟种真实存在的照片来源。
只输出 JSON 对象本身，不要输出 Markdown 代码块、解释或多余文字。

{
  "identified": true,
  "family": "鹭科",
  "familyLatin": "Ardeidae",
  "genus": "白鹭属",
  "genusLatin": "Egretta",
  "species": "白鹭",
  "speciesLatin": "Egretta garzetta",
  "commonNameEn": "Little Egret",
  "confidence": 0.92,
  "note": "一句话识别依据（羽色/喙/腿/体型等）",
  "alternatives": [
    {"species": "黄嘴白鹭", "speciesLatin": "Egretta eulophotes", "confidence": 0.04}
  ],
  "referenceImageUrl": "https://真实图片直链.jpg",
  "referenceSource": "来源站点名称",
  "referenceNote": "该参考照片的说明/来源页面"
}

规则：
1. family/genus/species 用规范中文名，*Latin 字段用拉丁学名；无法确定拉丁名时留空字符串。
2. confidence 为 0-1 之间的小数；不确定时给出最可能结果并降低 confidence，可附 1-2 个 alternatives。
3. 如果画面中没有鸟、或照片无法判断，返回 {"identified": false, "reason": "原因"}。
4. **参考照片**：referenceImageUrl 必须是「真实存在、可直接访问的图片文件直链」（以 .jpg/.jpeg/.png/.webp
   结尾，或明确返回图片内容的 URL），内容必须是该鸟种的真实照片；优先使用联网搜索得到的来源页面中的图片。
   参考来源页面填 referenceSource（如「中国鸟类图库」「鸟网」等），referenceNote 可写来源页面 URL。
   **不要使用维基百科/维基媒体（upload.wikimedia.org、wikipedia.org、commons.wikimedia.org）的图片**，
   这些域名在中国大陆通常无法访问；如果搜索结果只有维基图片，请继续联网搜索其它来源。
   如果无法确认真实图片直链，referenceImageUrl 必须留空字符串，**绝对不要编造或猜测 URL**。
5. 所有字段都必须出现；不要输出除 JSON 以外的任何内容。"""

USER_PROMPT = "请识别这张照片中的鸟类，只输出约定的 JSON。"

#: 首次结果没有可用参考图时，用同一次模型对话能力再要一个「境内可访问的真实图片直链」
REFERENCE_RETRY_PROMPT = (
    "请联网搜索鸟种「{species}」（{latin}）的真实照片，返回一个可直接访问的图片文件直链。"
    "要求：必须是真实存在的照片；优先中国境内可访问的来源（如中国鸟类图库、鸟网、各地自然博物馆、"
    "百度百科图片等）；**不要使用 upload.wikimedia.org / wikipedia.org / commons.wikimedia.org**。"
    "只输出 JSON："
    '{{"referenceImageUrl":"https://...jpg","referenceSource":"来源名","referenceNote":"来源页面URL"}}'
    "；如果找不到可访问的真实图片直链，referenceImageUrl 必须为空字符串，不要编造。"
)
