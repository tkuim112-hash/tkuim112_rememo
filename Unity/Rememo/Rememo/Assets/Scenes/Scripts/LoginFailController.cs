using System.Collections;
using UnityEngine;
using UnityEngine.UI;
using UnityEngine.SceneManagement;
using TMPro;

public class LoginFailController : MonoBehaviour
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
        // 見 LoginScreenController.cs 同一段註解：明確清空，不依賴欄位本來就是空的。
        emailInput.text = "";
        passwordInput.text = "";

        loginButton.onClick.AddListener(OnLoginClicked);
        // 見 LoginScreenController.cs 的同一段註解：觸控小鍵盤 Enter 送出時
        // TMP_InputField 同步文字進 .text 的時機沒有保證，晚一幀再讀值。
        emailInput.onSubmit.AddListener(_ => StartCoroutine(SubmitNextFrame()));
        passwordInput.onSubmit.AddListener(_ => StartCoroutine(SubmitNextFrame()));

        // 見 LoginScreenController.cs 同一段註解：imeCompositionMode 攔不住新版
        // Windows TSF 注音輸入法，改在字元寫入 .text 的那一刻做白名單過濾。
        emailInput.onSelect.AddListener(_ => Input.imeCompositionMode = IMECompositionMode.Off);
        emailInput.onDeselect.AddListener(_ => Input.imeCompositionMode = IMECompositionMode.Auto);
        passwordInput.onSelect.AddListener(_ => Input.imeCompositionMode = IMECompositionMode.Off);
        passwordInput.onDeselect.AddListener(_ => Input.imeCompositionMode = IMECompositionMode.Auto);
        emailInput.onValidateInput = RejectNonAscii;
        passwordInput.onValidateInput = RejectNonAscii;

        // 見 LoginScreenController.cs 同一段註解：即時清掉開頭空白，不用手動刪。
        emailInput.onValueChanged.AddListener(_ => StripLeadingSpace(emailInput));
        passwordInput.onValueChanged.AddListener(_ => StripLeadingSpace(passwordInput));

        // 預設密碼隱藏
        passwordInput.contentType = TMP_InputField.ContentType.Password;
        passwordInput.ForceLabelUpdate();

        if (togglePasswordButton != null)
        {
            togglePasswordButton.onClick.AddListener(TogglePasswordVisibility);
        }
        UpdateTogglePasswordLabel();

        // 顯示上一次登入失敗的實際原因（帳密錯誤 / 連線失敗等），顯示完即清除避免殘留給下次進場景用
        if (PlayerPrefs.HasKey("LoginErrorMessage"))
        {
            AuthService.ShowError(errorText, PlayerPrefs.GetString("LoginErrorMessage"));
            PlayerPrefs.DeleteKey("LoginErrorMessage");
        }
    }

    void TogglePasswordVisibility()
    {
        passwordVisible = !passwordVisible;
        passwordInput.contentType = passwordVisible
            ? TMP_InputField.ContentType.Standard
            : TMP_InputField.ContentType.Password;
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

    /// <summary>見 LoginScreenController.cs 同一段註解。</summary>
    static void StripLeadingSpace(TMP_InputField field)
    {
        if (field.text.Length == 0 || field.text[0] != ' ') return;

        int caret = field.caretPosition;
        field.text = field.text.TrimStart(' ');
        field.caretPosition = Mathf.Max(0, caret - 1);
    }

    void OnLoginClicked()
    {
        // 見 LoginScreenController.cs 同一段註解：組字佔位空白可能卡在欄位開頭。
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
        AuthService.ShowError(errorText, message);
    }
}
