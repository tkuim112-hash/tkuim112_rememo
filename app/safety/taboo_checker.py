"""
禁忌話題防護模組 — app/safety/taboo_checker.py

防護對象：AI 生成的回應內容（scene_text / question），不是長者說的話。
目的：確保 AI 不會主動聊到治療師事先設定的禁忌話題（Patient.taboo_words），
      即使 AI 沒有用到禁忌詞本身的字面，只要語意相關也要擋下來
      （例：taboo="家人離世"，AI問「你先生現在還好嗎」也算違規）。

架構：
  Layer 1 關鍵詞粗篩：同步、零延遲，抓字面直接命中的情況。
  Layer 2 LLM語意檢查：同步（必須在回應送出前完成，不能背景執行），
                       抓語意相關但沒用到禁忌詞字面的情況。

⚠️ 與 crisis_detector.py 的關鍵差異：
  這裡的 LLM 檢查是同步阻塞的，因為必須在 AI 回應「送出去給長者聽到」之前
  完成判斷；不像危機偵測可以背景執行、下一輪才生效。
  這代表每次生成都可能多一次 LLM 往返，會增加回應延遲，是刻意的取捨。
"""
import logging

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Layer 1：關鍵詞粗篩（同步，零 I/O）
# ══════════════════════════════════════════════════════════════════════

def keyword_prescan(text: str, taboo_words: list[str]) -> list[str]:
    """
    純字串比對，抓字面直接命中的情況。
    這層抓不到「語意相關但沒用到禁忌詞字面」的情況，
    那類需要靠 Layer 2 的 LLM 語意檢查。
    """
    return [w for w in taboo_words if w in text]


# ══════════════════════════════════════════════════════════════════════
# Layer 2：LLM 語意檢查（同步，必須在回應送出前完成）
# ══════════════════════════════════════════════════════════════════════

_TOPIC_CHECK_SYSTEM = (
    "你是內容安全檢查工具，負責判斷一段話「是否涉及」某些敏感話題的主題，"
    "不只看字面用詞，也要看語意內容是否相關。\n"
    "例如：禁忌主題是「家人離世」，即使句子沒有用到「離世」「過世」等字，"
    "只要是在問候某位已故親人、或暗示某位家人不在了，也算涉及該主題。"
)


def _build_topic_check_prompt(text: str, taboo_words: list[str]) -> str:
    taboo_str = "、".join(taboo_words)
    return (
        f"【禁忌主題】\n{taboo_str}\n\n"
        f"【待檢查內容】\n「{text}」\n\n"
        "請判斷這段內容是否涉及上述任一禁忌主題（包含語意相關、"
        "非直接使用禁忌詞字面的情況）。\n"
        "只回 YES 或 NO，不要任何說明。"
    )


async def llm_topic_check(text: str, taboo_words: list[str], llm_service) -> bool:
    """
    回傳 True 代表「有違規」（涉及禁忌主題），False 代表安全。
    失敗時 fail-safe：保守視為違規，觸發重新生成或退回安全語句，
    不能讓解析失敗變成「默默放行」。
    """
    if not taboo_words:
        return False
    prompt = _build_topic_check_prompt(text, taboo_words)
    try:
        messages = [
            {"role": "system", "content": _TOPIC_CHECK_SYSTEM},
            {"role": "user", "content": prompt},
        ]
        raw = await llm_service.chat(messages)
        return raw.strip().upper().startswith("Y")
    except Exception as e:
        logger.warning(f"[TabooChecker] LLM語意檢查失敗: {e}，保守視為違規")
        return True


# ══════════════════════════════════════════════════════════════════════
# 整合入口：包住既有的生成函式，加上檢查 + 重試 + 保底
# ══════════════════════════════════════════════════════════════════════

SAFE_FALLBACK_SCENE_TEXT = "我們換個輕鬆一點的方向聊聊吧。"
SAFE_FALLBACK_QUESTION = "今天過得還好嗎？"
SAFE_FALLBACK_CLOSING_TEXT = "謝謝您今天的分享，辛苦了。"


async def guarded_generate(
    generate_fn,
    taboo_words: list[str],
    llm_service,
    max_retry: int = 1,
    text_keys: tuple[str, ...] = ("scene_text", "question"),
    fallback: dict | None = None,
    **generate_kwargs,
) -> dict:
    """
    包住任一個「產生文字內容」的生成函式，加上禁忌話題防護。

    Args:
        generate_fn: 原本的生成方法（例如 self._generate_open_followup），
                     必須是 async，回傳一個 dict
        taboo_words: 這位長者的禁忌詞列表
        llm_service: LLMService 實例
        max_retry:   違規時重新生成的次數上限
        text_keys:   要納入檢查的欄位名稱（不同生成函式的回傳key不一樣，
                     例如 _generate_open_followup 用 scene_text/question，
                     _generate_closing 用 closing_text/question，必須對齊，
                     否則會漏檢查或保底語句 key 對不上導致呼叫端出錯）
        fallback:    違規重試後仍失敗時的保底回傳值，須包含與 generate_fn
                     相同的 key。未指定時預設使用 scene_text/question 保底，
                     若 text_keys 有換過，務必也提供對應的 fallback。
        **generate_kwargs: 原封不動轉給 generate_fn 的參數

    Returns:
        generate_fn 原本格式的 dict。若重試後仍違規，回傳 fallback。
    """
    if fallback is None:
        fallback = {
            "scene_text": SAFE_FALLBACK_SCENE_TEXT,
            "question": SAFE_FALLBACK_QUESTION,
        }

    if not taboo_words:
        return await generate_fn(**generate_kwargs)

    attempt = 0
    while attempt <= max_retry:
        result = await generate_fn(**generate_kwargs)
        combined = "".join(result.get(k, "") for k in text_keys)

        # Layer 1：先做便宜的字面檢查
        hits = keyword_prescan(combined, taboo_words)
        if hits:
            logger.warning(f"[TabooChecker] Layer1字面命中: {hits}，重新生成 (attempt={attempt})")
            attempt += 1
            continue

        # Layer 2：語意檢查（同步，較貴）
        violated = await llm_topic_check(combined, taboo_words, llm_service)
        if violated:
            logger.warning(f"[TabooChecker] Layer2語意違規，重新生成 (attempt={attempt})")
            attempt += 1
            continue

        return result

    logger.error(f"[TabooChecker] 重試{max_retry}次仍違規，退回安全保底語句")
    return fallback