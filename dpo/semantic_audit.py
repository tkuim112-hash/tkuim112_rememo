#!/usr/bin/env python3
"""語意/邏輯通順度稽核＋缺口檢查＋修復工具。

整合原本散落在暫存資料夾裡好幾支一次性腳本（semantic_audit.py / fix_semantic_128.py /
fix_track_a_64.py / generate_missing_52.py 等）的邏輯，收斂成單一、可重複執行、吃
命令列參數的正式工具。regex/格式層級檢查沿用 fix_xian_wording.py 跟 filter_data.py
既有的規則，這裡只新增「思考跟輸出對不對得上」「是不是一個好問題」這類 regex
抓不到、需要 LLM 語意判斷的檢查層。

用法：
  python dpo/semantic_audit.py scan
      對 train.jsonl 裡全部不重複 chosen 內容跑一次語意稽核，PASS/FAIL 統計印到
      終端機，FAIL 詳情寫進 --out 指定的檔案（預設 dpo/data/semantic_audit_fail.txt）。

  python dpo/semantic_audit.py gaps
      比對 scenarios.json / EMOTIONAL_SCENARIOS / TRACK_C_SCENARIOS / TRACK_D_SCENARIOS
      算出的理論key空間，跟 train.jsonl 實際涵蓋的key做差集，列出從沒被生成過的組合。

  python dpo/semantic_audit.py fix A:sc074:STEP2 B:b001 C:happy D:吳阿嬤
      丟棄指定 key 現有的資料（如果有），用帶 retry_feedback 的生成流程重新產生，
      驗證通過（validate_full）才寫回。key 格式：A:<scenario_id>:<STEP1|STEP2|STEP3>、
      B:<emotional_scenario_id>、C:<emotion_tone>、D:<elder_name>。

  python dpo/semantic_audit.py fix --from-scan
      丟棄並重新生成上一次 scan 找到的所有 FAIL key（讀 --out 那份報告檔）。

  python dpo/semantic_audit.py fix --from-gaps
      補生成上一次 gaps 找到的所有缺口 key（純新增，不discard，因為這些 key
      目前完全沒有任何版本）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import collect_data as cd
import filter_data as fd
import fix_xian_wording as fx

DATA_DIR = Path(__file__).parent / "data"
DEFAULT_FAIL_REPORT = DATA_DIR / "semantic_audit_fail.txt"
DEFAULT_GAPS_REPORT = DATA_DIR / "semantic_audit_gaps.txt"

MAX_ATTEMPTS = 5
MAX_REJECTED_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# 語意稽核標準（regex/格式層級以外，需要 LLM 判斷的部分）
# ---------------------------------------------------------------------------

AUDIT_SYSTEM = """你是嚴格的中文語言品質審查員，負責審查懷舊療法AI要對長者說的內容
（場景文字/承接語/問題/收尾語等）。這些內容會直接用TTS念給有輕微認知障礙的
長者聽，審查標準是：

1. 邏輯通順：如果內容裡有「思考：」欄位，檢查它描述的判斷/角度跟後面實際寫出來
   的場景文字、問題是不是真的對得上——常見錯誤是思考欄位說要問A，但問題卻寫成
   意思不同、對不上的B（例如思考說要問「什麼變化讓長者滿意」，但問題卻寫成
   「你最先看哪裡」，兩者語意不一致）
2. 語意通順：問題/場景文字/承接語本身念起來自然嗎？有沒有語意不清、邏輯跳躍、
   前後矛盾、用詞怪異、動作主體不合理（例如物件自己做了人的動作）的地方？
3. 情境連貫：場景文字/承接語有沒有跟問題實質相關？如果長者剛才說了什麼話
   （承接語／場景文字有提到），問題有沒有順著那個脈絡走，還是憑空硬轉話題？
4. 是不是一個好問題（語意層級，非格式層級，逐項檢查）：
   a. 預設答案：問題有沒有暗示答案、引導長者附和（例如「那一定很辛苦吧？」
      「這樣是不是很開心？」），而不是讓長者自己說出感受
   b. 錨點是否真實有關聯：問題開頭的物件／狀態，是不是真的跟後面問的內容
      有實質關係——自我檢查法：如果把這個物件／狀態換成畫面裡別的東西，
      後面這句問題是不是照樣通？如果照樣通，代表這個錨點只是語法上的
      開場白，沒有真的觸發那個問題，判定不合格
   c. 語意上是否暗示禁忌方向：即使沒有直接說出禁忌詞本身，問法或錨點的
      選擇是不是明顯在往【禁忌話題】的方向引導長者回憶
   d. 具體微觀，不抽象宏觀：問題是不是問一個具體的動作／片刻／小細節，
      而不是「說說你的看法」「聊聊你的感情」這種空泛大哉問
   e. 答案是否只有一個詞：問題是不是問「這是什麼」「這是誰」這種只用一個
      名詞（人名、地名、物品名）就能答完、答完就沒話講的封閉性問題
   f. 口語自然度：念起來像不像老朋友隨口問出來的一句話，而不是書面語、
      翻譯腔或制式問卷語氣

以上第4項刻意排除「是非題（嗎/是不是/有沒有/對不對）」「一問兩題（兩個問號）」
「超過字數上限」「錨點字面是否出現在畫面元素清單裡」「格式提示原文照抄」
「禁忌詞字面出現」這六種——這些已經有 fix_xian_wording.py / filter_data.py 的
regex 規則在檢查，這裡看到即使違反也不用重複標記，除非同時合併了上面 a~f 裡的
語意問題。"""


def build_audit_prompt(chosen: str, context: str) -> str:
    return f"""{context}

【要審查的內容】
{chosen}

請判斷這段內容的邏輯通順度、語意通順度，以及是不是一個好問題（見上述第4項
a~f）有沒有問題。只輸出以下兩種格式之一，不要輸出其他文字：

PASS

或

FAIL: 一句話具體說明哪裡不通順、為什麼（指出具體是哪一句話、違反第幾項
標準、邏輯或語意哪裡對不上）"""


def semantic_ok(chosen: str, context: str) -> tuple[bool, str]:
    prompt = build_audit_prompt(chosen, context)
    try:
        result = cd.call_claude(prompt, system=AUDIT_SYSTEM, model=cd.MODEL_CHOSEN)
    except Exception as e:
        return False, f"審查呼叫失敗：{e}"
    time.sleep(cd.REQUEST_DELAY)
    if result.strip().startswith("PASS"):
        return True, ""
    return False, result.strip()


def regex_checks_fail(content: str) -> str | None:
    """regex/格式層級檢查，沿用 fix_xian_wording.py / filter_data.py 既有規則，
    不重複實作。"""
    if fx.has_xian_wording(content):
        return "xian"
    if fx.has_zanmen_wording(content):
        return "zanmen"
    if fx.has_touyiju_wording(content):
        return "touyiju"
    if fx.has_bookish_verb_complement(content):
        return "bookish"
    if fx.has_scene_text_as_question(content):
        return "scene_as_question"
    if fd.has_leaked_self_check(content):
        return "self_check_leak"
    return None


def _describe_deterministic_fail(content: str, rule_name: str) -> str:
    """把確定性regex檢查的結果，展開成模型看得懂、能據以修正的具體說明——只回傳
    「FAIL:too_long」這種光禿禿的標籤，模型不知道超了幾個字、原句是什麼，等於
    沒拿到有用的回饋，retry_feedback 機制對這幾條規則就形同虛設。"""
    q = fx.extract_question(content)
    if rule_name == "too_long":
        length = len(fx._PUNCT_RE.sub("", q))
        return f"問題「{q}」共{length}字，超過15字上限{length - 15}字，請把這句話縮短到15字以內，可以拿掉不影響意思的修飾詞或合併語意重複的部分"
    if rule_name == "is_yesno":
        return f"問題「{q}」是是非題（用了「嗎／有沒有／是不是／會不會／要不要／對不對／好不好」這類句型），只能換一種開放式問法，不是刪掉這些字就好"
    if rule_name == "double_question":
        return f"問題「{q}」裡有兩個問號，等於一次問兩件事，長者會不知道先回答哪一個，請只保留一個問題"
    if rule_name == "memory_test":
        return f"問題「{q}」用「你還記得／記不記得」開頭，這是在測長者的記憶力而不是邀請他分享，請拿掉這個開頭、直接問內容本身"
    return rule_name


def validate_full(chosen: str, taboos: list[str], rules: dict[str, str], context: str) -> str:
    """完整驗證：格式regex → 內容regex → 規則LLM判斷 → 語意/好問題LLM判斷。
    回傳 "PASS" 或 "FAIL:<原因>"。"""
    det = fx.check_deterministic_rules(chosen)
    if det and det in rules:
        return f"FAIL:{det}:{_describe_deterministic_fail(chosen, det)}"
    r = regex_checks_fail(chosen)
    if r:
        return f"FAIL:{r}"
    v = fx.check_chosen_against_rules(chosen, rules, taboos=taboos)
    if v:
        return f"FAIL:{v}"
    ok, reason = semantic_ok(chosen, context)
    if not ok:
        return f"FAIL:semantic:{reason}"
    return "PASS"


# ---------------------------------------------------------------------------
# key 表示法：A key 是 ("A", scenario_id, step)；C 是 ("C", emotion_tone)；
# D 是 ("D", elder_name)；B 是 ("B", emotional_scenario_id)。字串格式：
# "A:sc074:STEP2"、"C:happy"、"D:吳阿嬤"、"B:b001"。
# 註：B 過去曾經整條track只用一個共用key ("B",)，導致 EMOTIONAL_SCENARIOS
# 61筆情境裡實際上只有第一筆被生成/稽核過，其餘60筆完全沒有訓練資料、
# 也沒被gaps/scan發現——2026-08-02 改成每筆情境各自一個key才修正。
# ---------------------------------------------------------------------------

def parse_key(s: str) -> tuple:
    parts = s.split(":")
    track = parts[0].upper()
    if track == "A":
        return ("A", parts[1], parts[2])
    if track in ("B", "C", "D"):
        return (track, parts[1])
    raise ValueError(f"無法解析的 key：{s}")


def format_key(key: tuple) -> str:
    return ":".join(str(x) for x in key)


def meta_to_key(m: dict) -> tuple | None:
    track = m.get("track")
    if track == "A":
        return ("A", m.get("scenario_id"), m.get("step"))
    if track == "B":
        return ("B", m.get("scenario_id"))
    if track == "C":
        return ("C", m.get("emotion_tone"))
    if track == "D":
        return ("D", m.get("scenario_id", "").replace("track_d_", ""))
    return None


def load_lookup_tables() -> tuple[dict, dict, dict, dict]:
    scenarios = json.loads(cd.SCENARIOS_FILE.read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in scenarios}
    track_b_by_id = {s["id"]: s for s in cd.EMOTIONAL_SCENARIOS}
    track_c_by_tone = {s["emotion_tone"]: s for s in cd.TRACK_C_SCENARIOS}
    track_d_by_name = {s["elder_name"]: s for s in cd.TRACK_D_SCENARIOS}
    return by_id, track_b_by_id, track_c_by_tone, track_d_by_name


def build_context(key: tuple, m: dict, by_id: dict, track_b_by_id: dict, track_c_by_tone: dict, track_d_by_name: dict) -> str:
    track = key[0]
    if track == "A":
        sc = by_id.get(key[1])
        if sc:
            return (
                f"【背景】長者：{sc['elder']['name']}，職業：{sc['elder']['main_occupation']}，"
                f"今日主題：{sc['elder']['today_topic']}，禁忌：{'、'.join(sc['elder'].get('taboos', [])) or '無'}\n"
                f"畫面元素：{'、'.join(sc['scene']['elements'])}\n步驟：{key[2]}"
            )
        return f"步驟：{key[2]}，禁忌：{'、'.join(m.get('taboos', [])) or '無'}"
    if track == "B":
        sc = track_b_by_id.get(key[1])
        trigger_context = sc.get("context", "") if sc else m.get("trigger_context", "")
        taboos = sc.get("taboos", []) if sc else m.get("taboos", [])
        return (
            f"【背景】情緒引導情境：{trigger_context}\n"
            f"禁忌：{'、'.join(taboos) or '無'}"
        )
    if track == "C":
        sc = track_c_by_tone.get(key[1])
        if sc:
            return (
                f"【背景】長者情緒：{key[1]}（{sc.get('emotion_desc', '')}）\n"
                f"長者剛說：「{sc.get('elder_response', '')}」\n"
                f"今日主題：{sc.get('current_topic', '')}，禁忌：{'、'.join(sc.get('taboos', [])) or '無'}\n"
                f"畫面元素：{'、'.join(sc.get('scene_elements', []))}"
            )
        return f"長者情緒：{key[1]}"
    if track == "D":
        sc = track_d_by_name.get(key[1])
        d_rule = (
            "問題規則：只能問「①現在的感受或心情／②今天最讓長者開心的回憶／③想帶走的"
            "正向感受」這三類其中一種，不能問其他類型的問題（例如操作細節、旁人是誰這類"
            "三回合對話裡才問的問題）；但每一類都必須具體呼應長者最後說的話裡提到的某個"
            "人事物或片刻，不能是換成任何一位長者、任何一段收尾發言都能原封不動問出口的"
            "通用句（例如「現在心裡感覺怎麼樣呢」「今天哪個故事讓你最開心」這類套死的句型，"
            "即使屬於允許的三類之一，沒有具體呼應內容一樣不合格）；即使做到具體呼應，也"
            "絕對不能用「是不是／想不想／會不會／對不對」這類確認型問法把問題包裝成看似"
            "呼應內容、實際上長者只能回答「是/不是」「想/不想」的是非題，一樣算違規"
        )
        if sc:
            return (
                f"【背景】三回合療程收尾，長者：{sc.get('elder_name')}，"
                f"今日主題：{sc.get('today_topic')}\n"
                f"長者最後說：「{sc.get('last_elder_response', '')}」\n"
                f"禁忌：{'、'.join(sc.get('taboos', [])) or '無'}\n"
                f"{d_rule}"
            )
        return f"三回合療程收尾，今日主題：{m.get('today_topic', '')}\n{d_rule}"
    return ""


def iter_unique_pairs(pairs: list[dict]):
    """dedup：同一個key只留第一次出現的那筆，回傳 {key: pair} 有序字典。"""
    seen: dict[tuple, dict] = {}
    for p in pairs:
        key = meta_to_key(p.get("meta", {}))
        if key is not None and key not in seen:
            seen[key] = p
    return seen


def expected_key_space() -> set[tuple]:
    scenarios = json.loads(cd.SCENARIOS_FILE.read_text(encoding="utf-8"))
    expected = set()
    for s in scenarios:
        for step in ("STEP1", "STEP2", "STEP3"):
            expected.add(("A", s["id"], step))
    for sc in cd.TRACK_C_SCENARIOS:
        expected.add(("C", sc["emotion_tone"]))
    for sc in cd.TRACK_D_SCENARIOS:
        expected.add(("D", sc["elder_name"]))
    for sc in cd.EMOTIONAL_SCENARIOS:
        expected.add(("B", sc["id"]))
    return expected


# ---------------------------------------------------------------------------
# 單一 key 的丟棄＋重新生成（帶 retry_feedback）
# ---------------------------------------------------------------------------

_STEP_BUILDERS = {
    "STEP1": cd.build_step1_user_prompt,
    "STEP2": cd.build_step2_user_prompt,
    "STEP3": cd.build_step3_user_prompt,
}
_STEP_COVERED_W = {"STEP1": [], "STEP2": ["Where"], "STEP3": ["Where", "Who"]}


def _regen_track_a(sid: str, step: str, by_id: dict, log) -> list[dict] | None:
    sc = by_id.get(sid)
    if sc is None:
        log(f"  ! 找不到 scenario {sid}")
        return None
    elder, scene = sc["elder"], sc["scene"]
    taboos = elder.get("taboos", [])
    key = ("A", sid, step)
    context = build_context(key, {}, by_id, {}, {}, {})

    chosen, feedback = None, ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            candidate = cd.call_claude(_STEP_BUILDERS[step](sc, retry_feedback=feedback))
        except Exception as e:
            log(f"  [{sid} {step}] 第{attempt}次：連線錯誤，重試（{e}）")
            time.sleep(3)
            continue
        time.sleep(cd.REQUEST_DELAY)
        r = validate_full(candidate, taboos, cd.QUESTION_REJECTION_RULES, context)
        log(f"  [{sid} {step}] 第{attempt}次：{r}")
        if r == "PASS":
            chosen = candidate
            break
        feedback = r
    if chosen is None:
        return None

    step_responses = {"STEP1": "", "STEP2": sc.get("elder_step1_response", ""), "STEP3": sc.get("elder_step2_response", "")}
    inference_prompt = cd.build_inference_prompt(
        step, elder, scene, _STEP_COVERED_W[step],
        topic_category=sc.get("topic_category"), elder_response=step_responses[step], taboos=taboos,
    )
    return _build_pairs(chosen, inference_prompt, cd.QUESTION_REJECTION_RULES, taboos, log,
                         meta_base={"scenario_id": sid, "step": step, "track": "A", "taboos": taboos},
                         rejection_prompt_builder=lambda rn, rd: cd.build_rejection_prompt(chosen, rn, rd, taboos=taboos))


def _regen_track_c(tone: str, track_c_by_tone: dict, log) -> list[dict] | None:
    sc = track_c_by_tone.get(tone)
    if sc is None:
        log(f"  ! 找不到 track_c {tone}")
        return None
    taboos = sc.get("taboos", [])
    context = build_context(("C", tone), {}, {}, {}, track_c_by_tone, {})

    chosen, feedback = None, ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            candidate = cd.call_claude(cd.build_track_c_chosen_prompt(sc, retry_feedback=feedback))
        except Exception as e:
            log(f"  [track_c {tone}] 第{attempt}次：連線錯誤，重試（{e}）")
            time.sleep(3)
            continue
        time.sleep(cd.REQUEST_DELAY)
        r = validate_full(candidate, taboos, cd.TRACK_C_REJECTION_RULES, context)
        log(f"  [track_c {tone}] 第{attempt}次：{r}")
        if r == "PASS":
            chosen = candidate
            break
        feedback = r
    if chosen is None:
        return None

    covered_w = cd._covered_w_before(sc["next_w"])
    inference_prompt = cd.build_track_c_inference_prompt(sc, covered_w=covered_w, skipped_w=[], taboos=taboos)
    return _build_pairs(chosen, inference_prompt, cd.TRACK_C_REJECTION_RULES, taboos, log,
                         meta_base={"scenario_id": f"track_c_{tone}", "step": "TRACK_C", "track": "C",
                                    "emotion_tone": tone, "taboos": taboos},
                         rejection_prompt_builder=lambda rn, rd: cd.build_track_c_rejection_prompt(chosen, rn, rd, taboos=taboos))


def _regen_track_d(name: str, track_d_by_name: dict, log) -> list[dict] | None:
    sc = track_d_by_name.get(name)
    if sc is None:
        log(f"  ! 找不到 track_d {name}")
        return None
    taboos = sc.get("taboos", [])
    context = build_context(("D", name), {}, {}, {}, {}, track_d_by_name)

    chosen, feedback = None, ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            candidate = cd.call_claude(cd.build_track_d_chosen_prompt(sc, retry_feedback=feedback))
        except Exception as e:
            log(f"  [track_d {name}] 第{attempt}次：連線錯誤，重試（{e}）")
            time.sleep(3)
            continue
        time.sleep(cd.REQUEST_DELAY)
        r = validate_full(candidate, taboos, cd.TRACK_D_REJECTION_RULES, context)
        log(f"  [track_d {name}] 第{attempt}次：{r}")
        if r == "PASS":
            chosen = candidate
            break
        feedback = r
    if chosen is None:
        return None

    emotion = sc.get("emotion", "happy")
    inference_prompt = cd.build_track_d_inference_prompt(sc, emotion=emotion)
    return _build_pairs(chosen, inference_prompt, cd.TRACK_D_REJECTION_RULES, taboos, log,
                         meta_base={"scenario_id": f"track_d_{name}", "step": "TRACK_D", "track": "D", "taboos": taboos},
                         rejection_prompt_builder=lambda rn, rd: cd.build_track_d_rejection_prompt(chosen, rn, rd, taboos=taboos))


def _regen_track_b(bid: str, track_b_by_id: dict, log) -> list[dict] | None:
    emo_sc = track_b_by_id.get(bid)
    if emo_sc is None:
        log(f"  ! 找不到 emotional scenario {bid}")
        return None
    trigger, context_desc, taboos = emo_sc["trigger"], emo_sc["context"], emo_sc.get("taboos", [])
    key = ("B", bid)
    context = build_context(key, {}, {}, track_b_by_id, {}, {})

    chosen, feedback = None, ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            candidate = cd.call_claude(cd.build_emotional_chosen_prompt(trigger, context_desc, taboos=taboos, retry_feedback=feedback))
        except Exception as e:
            log(f"  [{bid}] 第{attempt}次：連線錯誤，重試（{e}）")
            time.sleep(3)
            continue
        time.sleep(cd.REQUEST_DELAY)
        r = validate_full(candidate, taboos, cd.EMOTION_REJECTION_RULES, context)
        log(f"  [{bid}] 第{attempt}次：{r}")
        if r == "PASS":
            chosen = candidate
            break
        feedback = r
    if chosen is None:
        return None

    inference_prompt = cd.build_emotional_inference_prompt(trigger, taboos=taboos)
    return _build_pairs(chosen, inference_prompt, cd.EMOTION_REJECTION_RULES, taboos, log,
                         meta_base={"scenario_id": bid, "step": "EMOTIONAL", "track": "B",
                                    "trigger_context": context_desc, "taboos": taboos},
                         rejection_prompt_builder=lambda rn, rd: cd.build_emotional_rejection_prompt(chosen, rn, rd, taboos=taboos))


def _build_pairs(chosen, inference_prompt, rules, taboos, log, meta_base, rejection_prompt_builder) -> list[dict]:
    pairs = []
    for rule_name, rule_desc in rules.items():
        if rule_name in ("touches_taboo", "dwell_on_taboo") and not taboos:
            continue
        rejected = None
        for _ in range(MAX_REJECTED_ATTEMPTS):
            try:
                candidate = cd.call_claude(rejection_prompt_builder(rule_name, rule_desc), model=cd.MODEL_REJECTED)
                time.sleep(cd.REQUEST_DELAY)
            except Exception:
                continue
            if candidate != chosen:
                rejected = candidate
                break
        if rejected is None:
            log(f"    ✗ [{rule_name}] rejected 生成失敗，跳過")
            continue
        pairs.append({
            "prompt": inference_prompt,
            "chosen": [{"role": "assistant", "content": chosen}],
            "rejected": [{"role": "assistant", "content": rejected}],
            "meta": {**meta_base, "rejection_rule": rule_name},
        })
    return pairs


def regen_one(key: tuple, by_id: dict, track_b_by_id: dict, track_c_by_tone: dict, track_d_by_name: dict, log) -> list[dict] | None:
    track = key[0]
    if track == "A":
        return _regen_track_a(key[1], key[2], by_id, log)
    if track == "B":
        return _regen_track_b(key[1], track_b_by_id, log)
    if track == "C":
        return _regen_track_c(key[1], track_c_by_tone, log)
    if track == "D":
        return _regen_track_d(key[1], track_d_by_name, log)
    raise ValueError(f"未知 track：{track}")


# ---------------------------------------------------------------------------
# CLI 子命令
# ---------------------------------------------------------------------------

def cmd_scan(args) -> None:
    pairs = fx.load_existing()
    by_id, track_b_by_id, track_c_by_tone, track_d_by_name = load_lookup_tables()
    seen = iter_unique_pairs(pairs)
    print(f"待審查：{len(seen)} 筆不重複 chosen", flush=True)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pass_count = fail_count = 0
    with out_path.open("w", encoding="utf-8") as out:
        for i, (key, p) in enumerate(seen.items(), 1):
            m = p["meta"]
            chosen = p["chosen"][0]["content"]
            context = build_context(key, m, by_id, track_b_by_id, track_c_by_tone, track_d_by_name)
            prompt = build_audit_prompt(chosen, context)
            try:
                result = cd.call_claude(prompt, system=AUDIT_SYSTEM, model=cd.MODEL_CHOSEN)
            except Exception as e:
                result = f"ERROR: {e}"
            time.sleep(cd.REQUEST_DELAY)

            if result.strip().startswith("PASS"):
                pass_count += 1
            else:
                fail_count += 1
                out.write(f"=== {format_key(key)} ===\n{result.strip()}\n\n內容：\n{chosen}\n\n{'='*60}\n\n")
                out.flush()

            if i % 20 == 0:
                print(f"進度：{i}/{len(seen)}（PASS {pass_count}，FAIL {fail_count}）", flush=True)

    print(f"\n完成！PASS {pass_count}，FAIL {fail_count}", flush=True)
    print(f"詳細結果：{out_path}", flush=True)


def cmd_gaps(args) -> None:
    pairs = fx.load_existing()
    seen = iter_unique_pairs(pairs)
    expected = expected_key_space()
    actual = set(seen.keys())
    missing = expected - actual
    extra = actual - expected

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as out:
        for k in sorted(missing):
            out.write(format_key(k) + "\n")

    print(f"理論總key數：{len(expected)}｜實際涵蓋：{len(actual)}｜缺失：{len(missing)}｜多餘：{len(extra)}")
    if extra:
        print(f"警告，出現理論外的key（可能是scenarios.json改過還沒同步）：{[format_key(k) for k in sorted(extra)]}")
    print(f"缺失清單：{out_path}")


def cmd_fix(args) -> None:
    by_id, track_b_by_id, track_c_by_tone, track_d_by_name = load_lookup_tables()

    if args.from_scan:
        keys = _keys_from_report(Path(args.out))
        discard = True
    elif args.from_gaps:
        keys = [parse_key(line.strip()) for line in Path(args.gaps_out).read_text(encoding="utf-8").splitlines() if line.strip()]
        discard = False
    else:
        keys = [parse_key(k) for k in args.keys]
        discard = True

    if not keys:
        print("沒有要處理的 key，結束。")
        return
    print(f"待處理：{len(keys)} 組（{'丟棄後重新生成' if discard else '純新增，不discard'}）")

    existing = fx.load_existing()
    discarded_by_key: dict[tuple, list[dict]] = {}
    if discard:
        to_discard = set(keys)
        kept = []
        for p in existing:
            k = meta_to_key(p["meta"])
            if k in to_discard:
                discarded_by_key.setdefault(k, []).append(p)
            else:
                kept.append(p)
    else:
        kept = existing
    print(f"原有 {len(existing)} 筆，保留 {len(kept)} 筆")

    def log(msg: str) -> None:
        print(msg, flush=True)

    new_pairs: list[dict] = []
    still_failed: list[str] = []
    for i, key in enumerate(keys, 1):
        log(f"\n=== [{i}/{len(keys)}] {format_key(key)} ===")
        try:
            pairs = regen_one(key, by_id, track_b_by_id, track_c_by_tone, track_d_by_name, log)
        except Exception as e:
            log(f"  ! 例外：{e}")
            pairs = None
        if pairs:
            new_pairs.extend(pairs)
        else:
            still_failed.append(format_key(key))
            old_pairs = discarded_by_key.get(key)
            if old_pairs:
                log(f"  -> 重新生成失敗，保留舊版本（{len(old_pairs)} 筆），不留空缺")
                new_pairs.extend(old_pairs)

    all_pairs = kept + new_pairs
    with cd.OUTPUT_FILE.open("w", encoding="utf-8") as f:
        for pair in all_pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    print(f"\n完成！{'新增/替換' if discard else '新增'} {len(new_pairs)} 筆")
    print(f"train.jsonl 總計：{len(all_pairs)} 筆（原 {len(existing)} 筆）")
    print(f"這次處理 {len(keys)} 組，成功 {len(keys) - len(still_failed)} 組，仍失敗 {len(still_failed)} 組")
    if still_failed:
        print("仍失敗、需人工檢查：")
        for x in still_failed:
            print(f"  - {x}")


def _keys_from_report(report_path: Path) -> list[tuple]:
    if not report_path.exists():
        raise FileNotFoundError(f"找不到 scan 報告：{report_path}，先跑一次 scan")
    keys = []
    for line in report_path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^=== (.+?) ===$", line)
        if m:
            keys.append(parse_key(m.group(1)))
    return keys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="全量語意稽核")
    p_scan.add_argument("--out", default=str(DEFAULT_FAIL_REPORT), help="FAIL詳情輸出路徑")

    p_gaps = sub.add_parser("gaps", help="比對理論key空間，找出從沒生成過的組合")
    p_gaps.add_argument("--out", default=str(DEFAULT_GAPS_REPORT), help="缺失清單輸出路徑")

    p_fix = sub.add_parser("fix", help="丟棄並重新生成指定/scan找到/gaps找到的 key")
    p_fix.add_argument("keys", nargs="*", help='要修復的 key，例如 A:sc074:STEP2 C:happy D:吳阿嬤 B')
    p_fix.add_argument("--from-scan", action="store_true", help="處理上次 scan 找到的所有 FAIL")
    p_fix.add_argument("--from-gaps", action="store_true", help="補生成上次 gaps 找到的所有缺口")
    p_fix.add_argument("--out", default=str(DEFAULT_FAIL_REPORT), help="搭配 --from-scan：scan報告路徑")
    p_fix.add_argument("--gaps-out", default=str(DEFAULT_GAPS_REPORT), help="搭配 --from-gaps：gaps清單路徑")

    args = parser.parse_args()
    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "gaps":
        cmd_gaps(args)
    elif args.command == "fix":
        cmd_fix(args)


if __name__ == "__main__":
    main()
