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
    public string pre_image_q1_answer;
    public string pre_image_detail;
    public string[] known_facts_w;
    public string last_question_text;
    public string[] covered_senses;
    public string last_sense_asked;
    public string[] skipped_senses;
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
