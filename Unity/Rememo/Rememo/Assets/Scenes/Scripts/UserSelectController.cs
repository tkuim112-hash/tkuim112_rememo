using UnityEngine;
using UnityEngine.UI;
using UnityEngine.SceneManagement;
using TMPro;
using System.Collections;

public class UserSelectController : MonoBehaviour
{
    [Header("後端設定")]
    public string backendUrl = "http://localhost:8000";

    [Header("UI 元件")]
    public GameObject userCardPrefab;
    public Transform userGrid;
    public Button logoutButton;
    public TMP_Text topBarText;

    void Start()
    {
        logoutButton.onClick.AddListener(OnLogout);
        UpdateTopBar();
        StartCoroutine(PatientService.FetchPatients(backendUrl, OnPatientsLoaded, OnPatientsFail));
    }

    void UpdateTopBar()
    {
        if (topBarText == null) return;

        string orgName = string.IsNullOrEmpty(AuthSession.OrganizationName) ? "未指定機構" : AuthSession.OrganizationName;
        string therapistName = string.IsNullOrEmpty(AuthSession.TherapistName) ? "" : AuthSession.TherapistName;
        topBarText.text = $"{orgName} {therapistName}治療師";
    }

    void OnPatientsLoaded(PatientService.PatientSummary[] patients)
    {
        foreach (var patient in patients)
        {
            CreateUserCard(patient.id, patient.name, patient.age);
        }
    }

    void OnPatientsFail(string message)
    {
        Debug.LogWarning(message);
    }

    void CreateUserCard(int id, string name, int age)
    {
        GameObject card = Instantiate(userCardPrefab, userGrid);

        TMP_Text nameText = card.transform.Find("UserName").GetComponent<TMP_Text>();
        nameText.text = name;

        TMP_Text ageText = card.transform.Find("UserAge").GetComponent<TMP_Text>();
        ageText.text = $"年齡: {age}";

        Button cardButton = card.GetComponent<Button>();
        if (cardButton != null)
        {
            cardButton.onClick.AddListener(() => OnUserSelected(id, name));
        }
    }

    void OnUserSelected(int id, string userName)
    {
        Debug.Log($"選擇使用者：{userName}（id={id}）");
        PlayerPrefs.SetString("SelectedPatientId", id.ToString());
        // 選擇使用者後跳到 LoadingScene 再去 WarmupScene
        PlayerPrefs.SetString("NextScene", "WarmupScene");
        SceneManager.LoadScene("LoadingScene");
    }

    void OnLogout()
    {
        logoutButton.interactable = false;
        StartCoroutine(DoLogout());
    }

    IEnumerator DoLogout()
    {
        yield return StartCoroutine(AuthService.Revoke(backendUrl));
        AuthSession.Clear();
        SceneManager.LoadScene("LoginScene");
    }
}