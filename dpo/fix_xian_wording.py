#!/usr/bin/env python3
"""
修補 train.jsonl 裡 chosen 問題用了「先做什麼／先準備／先夾／先看」這種語法的
(scenario, step) 組合（Track A）跟 (emotion_tone) 組合（Track C）。

2026-07 使用者回饋：不喜歡「V之前/之後，你都先做什麼」這個「先」字，且這個句型
在範例庫裡佔比過高、生成結果同質化。已經修正：
  - app/prompts/question_5w1h.txt（正式環境 + Track A 的 inference prompt 共用）
  - dpo/collect_data.py 的 build_step1/2/3_user_prompt、build_track_c_chosen_prompt
    （這三支各自內嵌了一份獨立的規則文字，不是從 question_5w1h.txt 讀的，之前
    沒發現這裡也要修，改完 question_5w1h.txt 沒有同步改到這裡的話，重新生成
    出來的 chosen 還是會沿用舊語法）

這支腳本找出舊資料裡踩到「先」語法的 chosen，丟棄後用修好的 prompt 重新生成。

執行：
  python dpo/fix_xian_wording.py
"""
import json
import re
import sys
import time

import collect_data as cd
import filter_data as fd


# 舊版只列了9個特定動詞（做/夾/準備/備/看/想/說/走/怎麼），漏抓「先忙」這類清單外
# 的動詞（2026-07 使用者回饋：sc101 STEP1「你們都先忙什麼呢」就是漏網案例）。改成
# 「先」後面接任何字都算，只排除「先生／先夫／先父／先母／先人／先前／先天」這幾個
# 「先」是固定詞語一部分、不是「先+動詞」贅字用法的合法詞（例如「你先生以前都做
# 什麼工作呢？」問的是「先生」這個人，不是要拿掉的贅字「先」）。
_XIAN_RE = re.compile(r"(?<!最)先(?!生|夫|父|母|人|前|天)")
_ZANMEN_RE = re.compile(r"咱")
_DABASHOU_RE = re.compile(r"搭把手|搭一把手")
_SHOUCHANG_RE = re.compile(r"收場.{0,3}(回家|下班|下工)")
_TOUYIJU_RE = re.compile(r"頭一句(?!話)")
_BOOKISH_VC_RE = re.compile(r"做下來|說下去")
_SCENE_AS_QUESTION_RE = re.compile(r"(呢|嗎)[。！.!]?\s*$")

# 以下 4 條是 QUESTION_REJECTION_RULES / TRACK_C_REJECTION_RULES 裡結構性、
# 不需要語意判斷就能穩定判斷的規則，改用確定性 regex 而不是丟給 Haiku 判斷——
# 實測同一句明顯的是非題丟給 Haiku 判斷 4 次只抓到 1 次，這種格式層級的規則
# regex 比 LLM 判斷可靠得多。
_YESNO_END_RE = re.compile(r"嗎[？?]?\s*$")
_YESNO_PHRASE_RE = re.compile(r"有沒有|是不是|會不會|要不要|對不對|好不好|想不想")
_PUNCT_RE = re.compile(r"[，。、！？!?,.\s「」『』（）()]")
_MEMORY_TEST_START_RE = re.compile(r"^你?(還記得|記不記得)")

# 這 4 條交給 Haiku 語意判斷（覆蓋 QUESTION_REJECTION_RULES 扣掉上面 4 條後的
# 其餘 7 條：no_anchor、wrong_w_priority、leading_question、touches_taboo、
# treats_image_as_real、template_echo、elder_as_photo_subject）
_DETERMINISTIC_RULE_NAMES = {"is_yesno", "double_question", "too_long", "memory_test"}


def has_yesno_wording(content: str) -> bool:
    q = extract_question(content)
    return bool(_YESNO_END_RE.search(q)) or bool(_YESNO_PHRASE_RE.search(q))


def has_double_question(content: str) -> bool:
    q = extract_question(content)
    return (q.count("？") + q.count("?")) > 1


def has_too_long_question(content: str) -> bool:
    q = extract_question(content)
    return len(_PUNCT_RE.sub("", q)) > 15


def has_memory_test_wording(content: str) -> bool:
    q = extract_question(content)
    return bool(_MEMORY_TEST_START_RE.match(q))


def check_deterministic_rules(content: str) -> str | None:
    """回傳第一個命中的確定性規則名稱，全部通過回傳 None。"""
    if has_yesno_wording(content):
        return "is_yesno"
    if has_double_question(content):
        return "double_question"
    if has_too_long_question(content):
        return "too_long"
    if has_memory_test_wording(content):
        return "memory_test"
    return None


def extract_question(content: str) -> str:
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("問題："):
            return line[len("問題："):].strip()
        if line.startswith("承接語：") or line.startswith("場景文字："):
            continue
    return ""


def extract_scene_text(content: str) -> str:
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("場景文字："):
            return line[len("場景文字："):].strip()
    return ""


def has_xian_wording(content: str) -> bool:
    q = extract_question(content)
    return bool(_XIAN_RE.search(q))


def has_zanmen_wording(content: str) -> bool:
    """「咱們／咱」不限於問題句，承接語等其他欄位也可能出現，所以掃整段內容。"""
    return bool(_ZANMEN_RE.search(content))


def has_dabashou_wording(content: str) -> bool:
    """「搭把手」是北方/大陸口語，不限於問題句，掃整段內容。"""
    return bool(_DABASHOU_RE.search(content))


def has_shouchang_wording(content: str) -> bool:
    """「收場」接「回家/下班/下工」是誤用——「收場」是抽象語境（事情/戲怎麼收場），
    具體收拾東西離開要用「收工/收拾」，不限於問題句，掃整段內容。"""
    return bool(_SHOUCHANG_RE.search(content))


def has_touyiju_wording(content: str) -> bool:
    """「頭一句」省略「話」字語法不完整，同樣不限於問題句，掃整段內容。"""
    return bool(_TOUYIJU_RE.search(content))


def has_bookish_verb_complement(content: str) -> bool:
    """只攔已知踩到過的生硬動補搭配（做下來/說下去），不是窮舉所有書面翻譯腔。"""
    return bool(_BOOKISH_VC_RE.search(content))


def has_scene_text_as_question(content: str) -> bool:
    """場景文字偷埋問句（用「呢/嗎」結尾），會跟後面的「問題：」重複問兩次。
    只檢查「場景文字：」這一行，Track C 的「承接語：」不適用這條規則。
    """
    scene = extract_scene_text(content)
    return bool(scene) and bool(_SCENE_AS_QUESTION_RE.search(scene))


# 不屬於 QUESTION_REJECTION_RULES / TRACK_C_REJECTION_RULES、但同樣需要語意判斷
# 的額外檢查項目，併入同一次 Haiku 呼叫，不多花一次 API 成本。
_EXTRA_SEMANTIC_RULES = {
    "fabricated_biography": "在場景文字、思考、或問題裡把長者本人沒有根據的具體人生"
    "事實、事件或人際關係當成既定事實來寫（例如編造具體服務年資、跟誰的關係、某個"
    "事件的具體經過），而不是根據已知的長者背景資料或長者剛才親口說過的話",
}


def build_chosen_validation_prompt(
    chosen: str, rules: dict[str, str], taboos: list[str] | None = None
) -> str:
    taboo_str = "、".join(taboos) if taboos else "無"
    rules_text = "\n".join(f"{i + 1}. {name}：{desc}" for i, (name, desc) in enumerate(rules.items()))
    return f"""以下是一則懷舊治療 AI 要對長者說的問題回應，理論上應該是完全遵守規則的正面
示範（chosen）：

{chosen}

【這位長者的禁忌話題】
{taboo_str}

請檢查這則回應有沒有不小心違反下面任何一條規則。這些規則原本是設計來故意生成
「違規負面範例」用的，這裡要反過來用——檢查這則「理論上應該正確」的範例有沒有
意外也踩到其中任何一條：

{rules_text}

嚴格照這個格式輸出，不要多寫任何說明或理由：
如果沒有違反任何一條，只輸出：PASS
如果違反了其中一條或多條，輸出：FAIL: <違反的規則名稱，用逗號分隔>"""


def check_chosen_against_rules(
    chosen: str, rules: dict[str, str], taboos: list[str] | None = None
) -> str | None:
    """反向檢查 chosen 有沒有不小心違反 rules 裡任何一條規則（這些規則原本只用來
    生成 rejected 反例，從沒反過來驗證過 chosen 本身）。回傳 None 代表通過；
    否則回傳違規規則名稱（字串）。

    格式層級的規則（is_yesno/double_question/too_long/memory_test，見
    _DETERMINISTIC_RULE_NAMES）先用確定性 regex 判斷——實測同一句明顯的是非題
    丟給 Haiku 判斷 4 次只抓到 1 次，這類規則 regex 遠比 LLM 判斷可靠。剩下需要
    語意判斷的規則（no_anchor/wrong_w_priority/leading_question/touches_taboo/
    treats_image_as_real/template_echo/elder_as_photo_subject）才呼叫 Haiku。
    """
    det = check_deterministic_rules(chosen)
    if det and det in rules:
        return det

    llm_rules = {name: desc for name, desc in rules.items() if name not in _DETERMINISTIC_RULE_NAMES}
    llm_rules.update(_EXTRA_SEMANTIC_RULES)

    prompt = build_chosen_validation_prompt(chosen, llm_rules, taboos=taboos)
    try:
        result = cd.call_claude(prompt, model=cd.MODEL_REJECTED)
        time.sleep(cd.REQUEST_DELAY)
    except Exception as e:
        print(f"    ✗ chosen 規則驗證呼叫失敗：{e}（視為通過，不阻擋）")
        return None
    result = result.strip()
    if result.upper().startswith("PASS"):
        return None
    return result


def load_existing() -> list[dict]:
    if not cd.OUTPUT_FILE.exists():
        return []
    pairs = []
    with cd.OUTPUT_FILE.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                pairs.append(json.loads(line))
    return pairs


def _has_any_wording_issue_a(content: str) -> bool:
    """Track A 專用：含 scene_as_question（只檢查「場景文字：」這一行，Track C
    的「承接語：」不適用這條規則，所以獨立一支給 Track A 用，不要跟 Track C
    共用同一支檢查函式）。"""
    return (
        has_xian_wording(content)
        or has_zanmen_wording(content)
        or has_dabashou_wording(content)
        or has_shouchang_wording(content)
        or has_touyiju_wording(content)
        or has_bookish_verb_complement(content)
        or has_scene_text_as_question(content)
    )


def _has_any_wording_issue_c(content: str) -> bool:
    return (
        has_xian_wording(content)
        or has_zanmen_wording(content)
        or has_dabashou_wording(content)
        or has_shouchang_wording(content)
        or has_touyiju_wording(content)
        or has_bookish_verb_complement(content)
    )


def find_affected_track_a(existing: list[dict]) -> set[tuple[str, str]]:
    affected = set()
    for obj in existing:
        if obj["meta"]["track"] != "A":
            continue
        if _has_any_wording_issue_a(obj["chosen"][0]["content"]):
            affected.add((obj["meta"]["scenario_id"], obj["meta"]["step"]))
    return affected


def find_affected_track_c(existing: list[dict]) -> set[str]:
    affected = set()
    for obj in existing:
        if obj["meta"]["track"] != "C":
            continue
        if _has_any_wording_issue_c(obj["chosen"][0]["content"]):
            affected.add(obj["meta"]["emotion_tone"])
    return affected


def regenerate_track_a_scenario_step(sc: dict, step: str) -> list[dict]:
    """重跑 collect_data.py generate_track_a() 對單一 (scenario, step) 的邏輯。"""
    elder = sc["elder"]
    scene = sc["scene"]
    step_builders = {
        "STEP1": (cd.build_step1_user_prompt, [], []),
        "STEP2": (cd.build_step2_user_prompt, ["Where"], ["STEP1"]),
        "STEP3": (cd.build_step3_user_prompt, ["Where", "Who"], ["STEP1", "STEP2"]),
    }
    prompt_builder, covered_w, _ = step_builders[step]

    print(f"  [{sc['id']}] {step} — 重新生成 chosen...")
    user_prompt = prompt_builder(sc)
    try:
        chosen = cd.call_claude(user_prompt)
        time.sleep(cd.REQUEST_DELAY)
    except Exception as e:
        print(f"    ✗ chosen 失敗：{e}")
        return []

    if has_xian_wording(chosen):
        print("    ! 重新生成後仍是「先」語法，跳過此組（需要人工檢查）")
        return []
    if has_zanmen_wording(chosen):
        print("    ! 重新生成後出現「咱們／咱」，跳過此組（需要人工檢查）")
        return []
    if has_dabashou_wording(chosen):
        print("    ! 重新生成後出現「搭把手」，跳過此組（需要人工檢查）")
        return []
    if has_shouchang_wording(chosen):
        print("    ! 重新生成後出現「收場回家」誤用，跳過此組（需要人工檢查）")
        return []
    if has_touyiju_wording(chosen):
        print("    ! 重新生成後出現「頭一句」（省略話字），跳過此組（需要人工檢查）")
        return []
    if has_bookish_verb_complement(chosen):
        print("    ! 重新生成後出現生硬動補搭配（做下來/說下去），跳過此組（需要人工檢查）")
        return []
    if has_scene_text_as_question(chosen):
        print("    ! 重新生成後場景文字偷埋問句（呢/嗎結尾），跳過此組（需要人工檢查）")
        return []
    if fd.has_leaked_self_check(chosen):
        print("    ! 重新生成後偵測到自我檢查洩漏，跳過此組（需要人工檢查）")
        return []
    taboos = elder.get("taboos", [])
    violation = check_chosen_against_rules(chosen, cd.QUESTION_REJECTION_RULES, taboos=taboos)
    if violation:
        print(f"    ! chosen 違反規則檢查：{violation}，跳過此組（需要人工檢查）")
        return []

    step_responses = {
        "STEP1": "",
        "STEP2": sc.get("elder_step1_response", ""),
        "STEP3": sc.get("elder_step2_response", ""),
    }
    inference_prompt = cd.build_inference_prompt(
        step, elder, scene, covered_w,
        topic_category=sc.get("topic_category"),
        elder_response=step_responses[step],
        taboos=taboos,
    )

    pairs = []
    for rule_name, rule_desc in cd.QUESTION_REJECTION_RULES.items():
        if rule_name == "touches_taboo" and not taboos:
            continue
        print(f"    [{rule_name}] 生成 rejected...")
        rejection_prompt = cd.build_rejection_prompt(chosen, rule_name, rule_desc, taboos=taboos)
        rejected = None
        for attempt in range(3):
            try:
                candidate = cd.call_claude(rejection_prompt, model=cd.MODEL_REJECTED)
                time.sleep(cd.REQUEST_DELAY)
            except Exception as e:
                print(f"      ✗ rejected 失敗（attempt {attempt+1}）：{e}")
                continue
            if candidate == chosen:
                continue
            rejected = candidate
            break
        if rejected is None:
            print(f"      ✗ [{rule_name}] 三次均失敗或 chosen==rejected，跳過")
            continue
        pairs.append({
            "prompt": inference_prompt,
            "chosen": [{"role": "assistant", "content": chosen}],
            "rejected": [{"role": "assistant", "content": rejected}],
            "meta": {
                "scenario_id": sc["id"],
                "step": step,
                "rejection_rule": rule_name,
                "track": "A",
                "taboos": taboos,
            },
        })
    return pairs


def regenerate_track_c_scenario(sc: dict) -> list[dict]:
    """重跑 collect_data.py generate_track_c() 對單一情境的邏輯。"""
    taboos = sc.get("taboos", [])
    print(f"  [Track C / {sc['emotion_tone']}] 重新生成 chosen...")
    chosen_prompt = cd.build_track_c_chosen_prompt(sc)
    try:
        chosen = cd.call_claude(chosen_prompt)
        time.sleep(cd.REQUEST_DELAY)
    except Exception as e:
        print(f"    ✗ chosen 失敗：{e}")
        return []

    if has_xian_wording(chosen):
        print("    ! 重新生成後仍是「先」語法，跳過此組（需要人工檢查）")
        return []
    if has_zanmen_wording(chosen):
        print("    ! 重新生成後出現「咱們／咱」，跳過此組（需要人工檢查）")
        return []
    if has_dabashou_wording(chosen):
        print("    ! 重新生成後出現「搭把手」，跳過此組（需要人工檢查）")
        return []
    if has_shouchang_wording(chosen):
        print("    ! 重新生成後出現「收場回家」誤用，跳過此組（需要人工檢查）")
        return []
    if has_touyiju_wording(chosen):
        print("    ! 重新生成後出現「頭一句」（省略話字），跳過此組（需要人工檢查）")
        return []
    if has_bookish_verb_complement(chosen):
        print("    ! 重新生成後出現生硬動補搭配（做下來/說下去），跳過此組（需要人工檢查）")
        return []
    if fd.has_leaked_self_check(chosen):
        print("    ! 重新生成後偵測到自我檢查洩漏，跳過此組（需要人工檢查）")
        return []
    violation = check_chosen_against_rules(chosen, cd.TRACK_C_REJECTION_RULES, taboos=taboos)
    if violation:
        print(f"    ! chosen 違反規則檢查：{violation}，跳過此組（需要人工檢查）")
        return []

    covered_w = cd._covered_w_before(sc["next_w"])
    inference_prompt = cd.build_track_c_inference_prompt(sc, covered_w=covered_w, skipped_w=[], taboos=taboos)

    pairs = []
    for rule_name, rule_desc in cd.TRACK_C_REJECTION_RULES.items():
        if rule_name == "touches_taboo" and not taboos:
            continue
        print(f"    [{rule_name}] 生成 rejected...")
        rejection_prompt = cd.build_track_c_rejection_prompt(chosen, rule_name, rule_desc, taboos=taboos)
        rejected = None
        for attempt in range(3):
            try:
                candidate = cd.call_claude(rejection_prompt, model=cd.MODEL_REJECTED)
                time.sleep(cd.REQUEST_DELAY)
            except Exception as e:
                print(f"      ✗ rejected 失敗（attempt {attempt+1}）：{e}")
                continue
            if candidate == chosen:
                continue
            rejected = candidate
            break
        if rejected is None:
            print(f"      ✗ [{rule_name}] 三次均失敗或 chosen==rejected，跳過")
            continue
        pairs.append({
            "prompt": inference_prompt,
            "chosen": [{"role": "assistant", "content": chosen}],
            "rejected": [{"role": "assistant", "content": rejected}],
            "meta": {
                "scenario_id": f"track_c_{sc['emotion_tone']}",
                "step": "TRACK_C",
                "rejection_rule": rule_name,
                "track": "C",
                "emotion_tone": sc["emotion_tone"],
                "taboos": taboos,
            },
        })
    return pairs


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    existing = load_existing()
    print(f"現有 train.jsonl：{len(existing)} 筆")

    a_keys = find_affected_track_a(existing)
    c_keys = find_affected_track_c(existing)
    print(f"偵測到「先」語法的 Track A (scenario, step) 組合：{len(a_keys)} 組")
    for sid, step in sorted(a_keys):
        print(f"  - {sid} {step}")
    print(f"偵測到「先」語法的 Track C emotion_tone 組合：{len(c_keys)} 組")
    for tone in sorted(c_keys):
        print(f"  - {tone}")

    scenarios = json.loads(cd.SCENARIOS_FILE.read_text(encoding="utf-8"))
    by_id = {sc["id"]: sc for sc in scenarios}
    track_c_by_tone = {sc["emotion_tone"]: sc for sc in cd.TRACK_C_SCENARIOS}

    keep = [
        p for p in existing
        if not (
            (p["meta"]["track"] == "A" and (p["meta"]["scenario_id"], p["meta"]["step"]) in a_keys)
            or (p["meta"]["track"] == "C" and p["meta"].get("emotion_tone") in c_keys)
        )
    ]
    print(f"\n保留：{len(keep)} 筆（丟棄 {len(existing) - len(keep)} 筆「先」語法 pair）")

    new_pairs: list[dict] = []
    print("\n=== 重新生成受影響組合（Track A）===")
    for sid, step in sorted(a_keys):
        sc = by_id.get(sid)
        if sc is None:
            print(f"  ! 找不到 {sid}，跳過")
            continue
        new_pairs.extend(regenerate_track_a_scenario_step(sc, step))

    print("\n=== 重新生成受影響組合（Track C）===")
    for tone in sorted(c_keys):
        sc = track_c_by_tone.get(tone)
        if sc is None:
            print(f"  ! 找不到 track_c_{tone}，跳過")
            continue
        new_pairs.extend(regenerate_track_c_scenario(sc))

    all_pairs = keep + new_pairs
    with cd.OUTPUT_FILE.open("w", encoding="utf-8") as f:
        for pair in all_pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    print(f"\n完成！新生成 {len(new_pairs)} 筆，train.jsonl 總計 {len(all_pairs)} 筆")
    print(f"輸出：{cd.OUTPUT_FILE}")


if __name__ == "__main__":
    main()
