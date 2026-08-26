// 對應 app/services/rag_client.py MemoryResult：純量欄位（無巢狀結構），
// JsonUtility 才能正確接住陣列裡的每一筆。
[System.Serializable]
public class RagMemory
{
    public string text;
    public string summary;
    public string emotion_tag;
    public float importance;
    public string timestamp;
}

[System.Serializable]
public class SessionStateData
{
    public string user_id;
    public string session_id;
    public int round;
    public string[] scene_elements;
    public string scene_composition;
    public string[] covered_w;
    public string[] skipped_w;
    public string last_question_type;
    public string last_w_asked;
    public int question_number;
    public long question_asked_at;
    // question_count/supplement_count 是 orchestrator 的回合題數上限/補問上限
    // 計數器（見 app/orchestrator.py _MAX_SUPPLEMENT_PER_ROUND）。這個 class
    // 之前沒宣告這兩個欄位，JsonUtility 序列化/反序列化時會直接忽略掉 JSON
    // 裡對應不到欄位的資料，導致每次長者答完話、Unity 把 state 傳回後端時
    // 這兩個計數器都被丟棄、後端只能套用預設值 0，補問上限因此從未真正生效
    // 過（無限追問的根因）。跟 session.py SessionState pydantic model 的欄位
    // 保持同步是這裡的原則——後端每加一個要跨回合存活的 state 欄位，這裡就
    // 要跟著補上，不然就是同一種坑。
    public int question_count;
    public int supplement_count;
    public string topic_category;
    // 2026-08-27稽核：跟這個 class 其餘欄位同一種坑——round 1 開場分類過
    // 這個主題適合哪些感官（見 app/orchestrator.py _classify_topic_senses），
    // 沒宣告在這裡的話，round 2/3 用 topic_senses 判斷「這個主題還有哪些
    // 相關感官沒問過」時永遠只拿得到空 list，等於感官追蹤/追問機制在這個
    // 欄位上從未真正生效過。
    public string[] topic_senses;
    // 生圖前情境2成立、且主題是 sub_item granularity（例如「哀傷之事」）時
    // 分類出的子項目（見 app/orchestrator.py _classify_pre_image_sub_item），
    // 同樣沒宣告在這裡的話，pre_image_q2 情境2分支第二輪起讀到的
    // sub_item 永遠是 null，等於白分類一次、該主題的排除規則沒生效。
    public string pre_image_sub_item;
    // start_round 一開始就撈好的 RAG 候選記憶（見 app/orchestrator.py
    // _retrieve_candidate_memories），長者生圖前引導問題答得太空洞時，
    // _start_scene_after_detail 會退回讀這裡當生圖記憶來源。這個 class
    // 原本沒宣告這個欄位，導致跟上面 question_count 那次一樣的坑：後端
    // 傳來的候選記憶在 Unity 反序列化時就被丟棄，state 傳回後端時自然
    // 也不含這個欄位，退回 RAG 記憶的生圖分支永遠只拿到空 list，個人化
    // 記憶從未真正生效過。
    public RagMemory[] cached_rag_memories;
    public string pre_image_q1_answer;
    public string pre_image_detail;
    public string[] known_facts_w;
    public string last_question_text;
    public string[] covered_senses;
    public string last_sense_asked;
    public string[] skipped_senses;
    // 2026-08-27稽核（使用者回報回合1重複問到同一個W維度後追查發現）：
    // 跟上面 question_count/supplement_count 同一種坑——這個 class 原本沒宣告
    // 這四個欄位，JsonUtility 反序列化/序列化時會直接忽略掉 JSON 裡對應不到
    // 欄位的資料，導致每次長者答完生圖前Q2、Unity 把 state 傳回後端時，
    // pre_image_q2_scenario 永遠被丟棄回預設值 1（不是後端剛設定的 2）——
    // process_response 因此永遠誤判成「情境1」，每次都對長者剛回答過的
    // 內容重新呼叫一次 LLM 判斷涵蓋了哪些W維度（本身是非決定性的），同一個
    // 維度（例如「何時」）就可能在不同輪被判斷成「涵蓋」又「缺」，反覆被
    // 用不同措辭問第二次。pre_image_q2_round/pre_image_q2_max_rounds/
    // pre_image_q2_last_w 同理都要補上，才能讓 app/routers/session.py
    // SessionState 對應的這四個欄位真正跨回合存活。
    public int pre_image_q2_scenario;
    public int pre_image_q2_round;
    public int pre_image_q2_max_rounds;
    public string pre_image_q2_last_w;
}

[System.Serializable]
public class StartRoundResponse
{
    public string user_name;
    public string today_topic;
    public string scene_text;
    public string scene_audio_path;
    // scene_text 目前是固定句（例如「很高興今天能坐下來陪你聊聊天。」），
    // 命中 app/services/audio_bank.py 時後端不即時TTS、scene_audio_path
    // 是null，改用這個 key 播內建音檔（見 LocalAudioPlayer）。
    public string scene_audio_key;
    public string image_path;
    public string question;
    public string audio_path;       // = question 的音檔路徑，既有欄位維持相容
    // Q1邀請語帶著治療師自由輸入的今日主題，沒辦法整句預錄：audio_path是
    // 「說到{今日主題}，」這段動態前綴的即時TTS結果，question_audio_key是
    // 後半段固定邀請語的內建音檔 key，播放順序是先 audio_path 再這個 key
    // （見 orchestrator.py _build_pre_image_question 說明）。
    public string question_audio_key;
    public SessionStateData state;
}

/// <summary>
/// InstructionScene 在等治療師端「啟動療程」時，順便把第一回合資料（命中後端快取，
/// 幾乎即時）先拉好放這裡；GameScene 進場時如果看到這裡有資料就直接拿來用，
/// 不用自己再打一次 API、顯示自己的 loadingSpinner。
/// </summary>
public static class PendingSessionStart
{
    public static StartRoundResponse Response;
}
