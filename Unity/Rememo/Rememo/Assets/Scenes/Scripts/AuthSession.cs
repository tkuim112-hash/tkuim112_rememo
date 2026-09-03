/// <summary>
/// 治療師登入後的 JWT 與基本資訊，整個 App 執行期間共用（不落地存檔，重開 App 需重新登入）。
/// </summary>
public static class AuthSession
{
    public static string Token;
    public static int TherapistId;
    public static string TherapistName;
    public static int OrganizationId;
    public static string OrganizationName;

    // 2026-08-21：原本存 PlayerPrefs，重開 App 後會留著，但 Token 不會——變成
    // 「session_id還在、token沒了」的半殘狀態，Kinect端拿舊session_id硬接
    // /ws/stt 永遠帶不了token、403到底，治療師端也連不上（稽核見 KinectAudioSender
    // 2026-08-20 10-16點兩次連線事故）。改成跟 Token 同生命週期：App重啟一起歸零，
    // 逼現場人員走正常復原流程（重新登入→重選病患→SessionService.FetchPendingSession
    // 向後端撈回跟治療師網頁共用的同一個session_id），而不是帶著錯的身分硬連。
    public static string SessionId;

    public static bool IsLoggedIn => !string.IsNullOrEmpty(Token);

    public static void Clear()
    {
        Token = null;
        TherapistId = 0;
        TherapistName = null;
        OrganizationId = 0;
        OrganizationName = null;
        SessionId = null;
    }
}
