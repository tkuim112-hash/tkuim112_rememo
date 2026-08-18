[System.Serializable]
public class SessionStateData
{
    public string user_id;
    public string session_id;
    public int round;
    public string[] scene_elements;
    public string[] covered_w;
    public string[] skipped_w;
    public string last_question_type;
    public string last_w_asked;
    public int question_number;
    public long question_asked_at;
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
