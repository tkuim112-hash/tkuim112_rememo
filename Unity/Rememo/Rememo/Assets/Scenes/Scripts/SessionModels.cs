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
    public string image_path;
    public string question;
    public string audio_path;
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
