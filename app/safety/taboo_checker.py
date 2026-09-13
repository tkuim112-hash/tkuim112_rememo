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

⚠️ 這裡只放禁忌話題本身的檢查。跟禁忌話題無關的內容格式/品質規則（是非題、
太長、把AI示意圖當成真實地點等）連同重試整合入口 guarded_generate，都在
app/safety/response_guard.py，兩者概念不同，2026-08 拆開成兩個檔案。
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
        # temperature=0：這是一個「只回YES/NO」的二元判斷，不是需要多樣性
        # 的生成任務。沒指定溫度時吃 llm.py 預設的 TEMPERATURE=0.3，實測
        # 發現同一句完全固定不變的文字（例如orchestrator.py寫死的分類1
        # 過渡句「聽你這樣說，我彷彿也看到了當時的畫面。」）反覆檢查，
        # 判定結果會不一致——同樣輸入卻有時候過、有時候被判違規，长者
        # 明明講的是做料理，卻偶爾被判成涉及跟主題完全無關的政治禁忌詞。
        # 降到0讓這個判斷盡量趨於決定性，減少純粹因為取樣隨機性造成的
        # 誤判（不保證100%決定性，但比0.3的隨機程度低很多）。
        raw = await llm_service.chat(messages, temperature=0)
        return raw.strip().upper().startswith("Y")
    except Exception as e:
        logger.warning(f"[TabooChecker] LLM語意檢查失敗: {e}，保守視為違規")
        return True
