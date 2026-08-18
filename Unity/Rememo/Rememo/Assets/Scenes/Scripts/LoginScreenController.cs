using System.Collections;
using UnityEngine;
using UnityEngine.UI;
using UnityEngine.SceneManagement;
using TMPro;

public class LoginScreenController : MonoBehaviour
{
    [Header("後端設定")]
    public string backendUrl = "https://api.re-memo.com";

    [Header("UI 元件")]
    public TMP_InputField emailInput;
    public TMP_InputField passwordInput;
    public Button loginButton;
    public TMP_Text errorText;

    [Header("密碼顯示/隱藏（文字按鈕，可留空不用）")]
    public Button togglePasswordButton;
    public TMP_Text togglePasswordLabel;

    bool passwordVisible = false;

    void Start()
    {
        // 場景剛進來時欄位有時會殘留一個字元（backspace 在還沒打字前就退得掉一格），
        // 不管是哪裡來的殘留狀態，這裡明確清空，不依賴欄位「本來就該是空的」。
        emailInput.text = "";
        passwordInput.text = "";

        loginButton.onClick.AddListener(OnLoginClicked);
        // 按觸控小鍵盤的 Enter/完成鍵觸發 onSubmit 時，TMP_InputField 同步
        // TouchScreenKeyboard 緩衝文字進 .text 的時機沒有保證在 onSubmit 之前完成，
        // 直接在這裡讀 .text 可能讀到少了最後一兩個字的舊值（帳密都打對，
        // 卻因為送出的字串不完整被後端判定密碼錯誤）。晚一幀再讀就能確保
        // 同步已經跑完。按鈕點擊不會有這個競速問題，故 OnLoginClicked 本身不用改。
        emailInput.onSubmit.AddListener(_ => StartCoroutine(SubmitNextFrame()));
        passwordInput.onSubmit.AddListener(_ => StartCoroutine(SubmitNextFrame()));
        passwordInput.contentType = TMP_InputField.ContentType.Password;
        passwordInput.ForceLabelUpdate();

        // 帳號/密碼欄位只吃半形英數，但作業系統預設輸入法可能是注音。imeCompositionMode
        // 只能控制 Unity 自己要不要顯示組字視窗，攔不住新版 Windows TSF 注音輸入法——
        // 實測關掉後還是會組出注音符號塞進欄位，導致送出的字串跟實際密碼不符，
        // 「明明打對密碼卻登入失敗」。改在字元真正要寫入 .text 的那一刻做白名單過濾，
        // 不管輸入法組出什麼，非半形可列印字元一律擋下，從根本避免非法字元進到欄位。
        emailInput.onSelect.AddListener(_ => Input.imeCompositionMode = IMECompositionMode.Off);
        emailInput.onDeselect.AddListener(_ => Input.imeCompositionMode = IMECompositionMode.Auto);
        passwordInput.onSelect.AddListener(_ => Input.imeCompositionMode = IMECompositionMode.Off);
        passwordInput.onDeselect.AddListener(_ => Input.imeCompositionMode = IMECompositionMode.Auto);
        emailInput.onValidateInput = RejectNonAscii;
        passwordInput.onValidateInput = RejectNonAscii;

        // Trim() 只在送出當下處理，欄位裡那個組字佔位空白平常還是看得到、需要手動刪。
        // 這裡改成文字一有變動就即時清掉開頭空白，空白不會留在畫面上。
        emailInput.onValueChanged.AddListener(_ => StripLeadingSpace(emailInput));
        passwordInput.onValueChanged.AddListener(_ => StripLeadingSpace(passwordInput));

        if (togglePasswordButton != null)
        {
            togglePasswordButton.onClick.AddListener(TogglePasswordVisibility);
        }
        UpdateTogglePasswordLabel();
    }

    void TogglePasswordVisibility()
    {
        passwordVisible = !passwordVisible;
        passwordInput.contentType = passwordVisible
            ? TMP_InputField.ContentType.Standard
            : TMP_InputField.ContentType.Password;
        // 切換 contentType 後要強制重繪，不然輸入框顯示的文字不會立即更新
        passwordInput.ForceLabelUpdate();
        UpdateTogglePasswordLabel();
    }

    void UpdateTogglePasswordLabel()
    {
        if (togglePasswordLabel != null)
        {
            togglePasswordLabel.text = passwordVisible ? "隱藏" : "顯示";
        }
    }

    IEnumerator SubmitNextFrame()
    {
        yield return null;
        OnLoginClicked();
    }

    /// <summary>擋下所有非半形可列印字元（0x20~0x7E），不管是不是輸入法組字組出來的。</summary>
    static char RejectNonAscii(string text, int charIndex, char addedChar)
    {
        return (addedChar >= 0x20 && addedChar <= 0x7E) ? addedChar : '\0';
    }

    /// <summary>
    /// 即時清掉欄位開頭的空白（輸入法組字佔位留下的），避免使用者要手動按 backspace 才能繼續打字。
    /// 用 text[0] 判斷才改 .text，避免每次打字都觸發、跟 onValueChanged 互相遞迴。
    /// </summary>
    static void StripLeadingSpace(TMP_InputField field)
    {
        if (field.text.Length == 0 || field.text[0] != ' ') return;

        int caret = field.caretPosition;
        field.text = field.text.TrimStart(' ');
        field.caretPosition = Mathf.Max(0, caret - 1);
    }

    void OnLoginClicked()
    {
        // 輸入法組字進行中時 TMP_InputField 會先塞一個空白佔位、等組字結果確定
        // 才替換掉；如果組字結果被 RejectNonAscii 擋下（例如打到一半跳出注音），
        // 那個佔位空白就會卡在欄位開頭沒被換掉。Trim 掉頭尾空白避免因此登入失敗
        // ——帳密欄位本來就不該有意義地包含前後空白。
        string email = emailInput.text.Trim();
        string password = passwordInput.text.Trim();

        if (string.IsNullOrEmpty(email))
        {
            AuthService.ShowError(errorText, "請輸入帳號");
            return;
        }

        if (string.IsNullOrEmpty(password))
        {
            AuthService.ShowError(errorText, "請輸入密碼");
            return;
        }

        loginButton.interactable = false;
        StartCoroutine(AuthService.Login(backendUrl, email, password, OnLoginSuccess, OnLoginFail));
    }

    void OnLoginSuccess(AuthService.LoginResponseBody resp)
    {
        AuthSession.Token = resp.token;
        AuthSession.TherapistId = resp.therapist_id;
        AuthSession.TherapistName = resp.name;
        AuthSession.OrganizationId = resp.organization_id;
        AuthSession.OrganizationName = resp.organization_name;

        Debug.Log($"登入成功：{resp.name}");
        PlayerPrefs.SetString("NextScene", "UserSelectScene");
        SceneManager.LoadScene("LoadingScene");
    }

    void OnLoginFail(string message)
    {
        loginButton.interactable = true;
        PlayerPrefs.SetString("LoginErrorMessage", message);
        SceneManager.LoadScene("LoginFailScene");
    }
}