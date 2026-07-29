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
        loginButton.onClick.AddListener(OnLoginClicked);
        emailInput.onSubmit.AddListener(_ => OnLoginClicked());
        passwordInput.onSubmit.AddListener(_ => OnLoginClicked());
        passwordInput.contentType = TMP_InputField.ContentType.Password;
        passwordInput.ForceLabelUpdate();

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

    void OnLoginClicked()
    {
        string email = emailInput.text;
        string password = passwordInput.text;

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