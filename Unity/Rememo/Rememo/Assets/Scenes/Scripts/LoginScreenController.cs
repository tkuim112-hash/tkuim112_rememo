using UnityEngine;
using UnityEngine.UI;
using UnityEngine.SceneManagement;
using TMPro;

public class LoginScreenController : MonoBehaviour
{
    [Header("後端設定")]
    public string backendUrl = "http://localhost:8000";

    [Header("UI 元件")]
    public TMP_InputField emailInput;
    public TMP_InputField passwordInput;
    public Button loginButton;
    public TMP_Text errorText;

    void Start()
    {
        loginButton.onClick.AddListener(OnLoginClicked);
        emailInput.onSubmit.AddListener(_ => OnLoginClicked());
        passwordInput.onSubmit.AddListener(_ => OnLoginClicked());
        passwordInput.contentType = TMP_InputField.ContentType.Password;
        passwordInput.ForceLabelUpdate();
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