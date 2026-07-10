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

    public static bool IsLoggedIn => !string.IsNullOrEmpty(Token);

    public static void Clear()
    {
        Token = null;
        TherapistId = 0;
        TherapistName = null;
        OrganizationId = 0;
        OrganizationName = null;
    }
}
