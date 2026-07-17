"""
場景元素防護模組 — app/safety/element_filter.py

防護對象：_plan_image() 規劃出的場景元素清單，避免元素落入
擴散模型（Stability AI）不可靠繪製、或長者根本答不出來的類別：
  - 需要辨識文字的元素（黑板文字、招牌字樣、書頁內容…）
  - 需要辨識特定人物身份/表情的元素（小人物、遠處人臉…）
  - 太小、太瑣碎，擴散模型容易畫糊的細節物件（零件、鈕扣、指針…）

前兩類元素即使被畫出來，畫面通常是模糊、扭曲或無意義的（擴散模型的
已知弱點，也是 services/image.py 的 negative_prompt 已經在排除的類別），
但 _generate_question 會機械式地要求問題開頭錨定在 scene_elements 的
第一個「畫面中看得到的具體物件」，若元素清單本身不可靠，長者就會被
問到一個他答不出來、甚至畫面裡根本看不清楚的細節（例如「黑板上寫
什麼」「照片裡的小男人是誰」）。第三類（細小物件）是同一個病根的延伸：
互動設計應用於失智老人懷舊治療之研究一文的原型 v1 失敗經驗也記錄了
「物件顯示太小」導致長者無法辨識，跟小人物是同一類問題，一併擋掉。

架構比照 taboo_checker.py：
  Layer 1 關鍵詞黑名單：同步、零延遲，濾掉字面明顯落入不可靠類別的元素。
  被濾掉後元素數量不足時，用安全備援元素池補足，不額外呼叫 LLM。
"""
import logging
import random

logger = logging.getLogger(__name__)

# 落入這些關鍵詞的元素，代表需要「讀取文字」或「辨識特定人物身份/表情」，
# 擴散模型很容易畫成模糊亂碼或扭曲人形，長者答不出來、畫面也看不清楚。
UNRELIABLE_ELEMENT_KEYWORDS = [
    # 需要讀取文字
    "字", "文字", "寫", "招牌", "黑板", "標語", "告示", "報紙", "書頁",
    "書本內容", "字幕", "門牌", "價目表", "菜單",
    # 需要辨識特定人物身份/表情/遠處小人物
    "人臉", "表情", "臉部", "小人", "小男人", "小女人", "遠處人影", "人群裡的",
    # 太小/太瑣碎，擴散模型容易畫糊、長者也看不清楚的細節物件
    "零件", "鈕扣", "指針", "刻度", "細節", "花紋", "紋路", "小物件", "小型",
]

# 濾掉不可靠元素後，若元素數量不足，從這個安全備援池隨機補足
# （形狀明確、不需要辨識文字或人臉細節，各年代場景都適用的大範圍實體物件）
SAFE_FALLBACK_ELEMENTS = [
    "腳踏車", "灶", "竹籃", "稻田", "老樹", "磚牆", "木桌", "扁擔",
    "斗笠", "水缸", "紅磚道", "曬穀場", "木窗", "石階", "鐵皮屋頂", "菜園",
]


def has_unreliable_category(element: str) -> bool:
    """判斷元素是否落入「需要讀取文字」「需要辨識特定人物/表情」或「太小太瑣碎」這幾類不可靠類別。"""
    return any(kw in element for kw in UNRELIABLE_ELEMENT_KEYWORDS)


def filter_scene_elements(elements: list[str], min_count: int = 4) -> list[str]:
    """
    濾掉不可靠類別的元素，數量不足時用安全備援元素補足。

    Args:
        elements: _plan_image() 規劃出的原始元素清單
        min_count: 至少要保留的元素數量（對齊 _plan_image prompt 要求的 4 個）

    Returns:
        濾掉不可靠元素、且數量足夠的新元素清單
    """
    kept = [e for e in elements if not has_unreliable_category(e)]
    removed = [e for e in elements if e not in kept]
    if removed:
        logger.warning(f"[ElementFilter] 濾除不可靠場景元素: {removed}")

    if len(kept) >= min_count:
        return kept

    pool = [e for e in SAFE_FALLBACK_ELEMENTS if e not in kept]
    random.shuffle(pool)
    kept.extend(pool[: min_count - len(kept)])
    return kept
