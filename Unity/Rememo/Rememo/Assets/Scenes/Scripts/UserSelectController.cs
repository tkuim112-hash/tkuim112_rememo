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

    [Header("預設頭貼（患者沒有上傳頭貼時使用，依患者 id 固定挑一張，同一位患者每次看到的都一樣）")]
    public Sprite[] defaultAvatars;

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
        // 依序建立卡片時記住前兩張分配到的預設頭貼 index，
        // 確保連續三張卡片（含自己）不會拿到同一張預設頭貼。
        int prevIndex1 = -1;
        int prevIndex2 = -1;

        foreach (var patient in patients)
        {
            int avatarIndex = ResolveAvatarIndex(patient.id, prevIndex1, prevIndex2);
            CreateUserCard(patient.id, patient.name, patient.age, patient.avatar, avatarIndex);

            prevIndex2 = prevIndex1;
            prevIndex1 = avatarIndex;
        }
    }

    void OnPatientsFail(string message)
    {
        Debug.LogWarning(message);
    }

    void CreateUserCard(int id, string name, int age, string avatarData, int avatarIndex)
    {
        GameObject card = Instantiate(userCardPrefab, userGrid);

        TMP_Text nameText = card.transform.Find("UserName").GetComponent<TMP_Text>();
        nameText.text = name;

        TMP_Text ageText = card.transform.Find("UserAge").GetComponent<TMP_Text>();
        ageText.text = $"年齡: {age}";

        Image photoImage = card.transform.Find("UserPhoto").GetComponent<Image>();
        SetAvatar(photoImage, avatarIndex, avatarData);

        Button cardButton = card.GetComponent<Button>();
        if (cardButton != null)
        {
            cardButton.onClick.AddListener(() => OnUserSelected(id, name));
        }
    }

    /// <summary>
    /// 患者有自己上傳頭貼（後端存的是 base64 data URL）就解碼顯示，否則用預先算好的
    /// avatarIndex 從 defaultAvatars 挑一張。
    /// </summary>
    void SetAvatar(Image photoImage, int avatarIndex, string avatarData)
    {
        if (photoImage == null) return;

        if (!string.IsNullOrEmpty(avatarData))
        {
            Sprite uploaded = DecodeBase64Avatar(avatarData);
            if (uploaded != null)
            {
                photoImage.sprite = uploaded;
                photoImage.color = Color.white;
                return;
            }
        }

        if (defaultAvatars != null && avatarIndex >= 0 && avatarIndex < defaultAvatars.Length)
        {
            photoImage.sprite = defaultAvatars[avatarIndex];
            photoImage.color = Color.white;
        }
    }

    /// <summary>
    /// 用雜湊把 patientId 打散成 defaultAvatars 的 index（同一個 id 永遠得到同一個基礎 index，
    /// 不會像單純取餘數那樣，id 遞增時規律地循環）；如果跟前兩張卡片撞號，
    /// 就依序往後挪到第一個沒撞號的 index，確保連續三張卡片不會重複
    /// （defaultAvatars 至少要放 3 張圖才能保證一定避開）。
    /// </summary>
    int ResolveAvatarIndex(int patientId, int prevIndex1, int prevIndex2)
    {
        if (defaultAvatars == null || defaultAvatars.Length == 0) return -1;

        int count = defaultAvatars.Length;
        int index = HashToIndex(patientId, count);

        int attempts = 0;
        while ((index == prevIndex1 || index == prevIndex2) && attempts < count)
        {
            index = (index + 1) % count;
            attempts++;
        }
        return index;
    }

    static int HashToIndex(int id, int count)
    {
        unchecked
        {
            uint h = (uint)id;
            h = (h ^ 61) ^ (h >> 16);
            h += h << 3;
            h ^= h >> 4;
            h *= 0x27d4eb2d;
            h ^= h >> 15;
            return (int)(h % (uint)count);
        }
    }

    Sprite DecodeBase64Avatar(string dataUrl)
    {
        try
        {
            int commaIndex = dataUrl.IndexOf(',');
            string base64 = commaIndex >= 0 ? dataUrl.Substring(commaIndex + 1) : dataUrl;
            byte[] bytes = System.Convert.FromBase64String(base64);

            Texture2D tex = new Texture2D(2, 2);
            if (!tex.LoadImage(bytes)) return null;

            // 長方形照片置中裁成正方形，效果等同前端 CSS 的 object-cover，
            // 裁掉的只是多出來的部分，正方形範圍內仍是原始解析度、不會失真。
            int cropSize = Mathf.Min(tex.width, tex.height);
            float xOffset = (tex.width - cropSize) / 2f;
            float yOffset = (tex.height - cropSize) / 2f;
            Rect cropRect = new Rect(xOffset, yOffset, cropSize, cropSize);

            return Sprite.Create(tex, cropRect, new Vector2(0.5f, 0.5f));
        }
        catch (System.Exception e)
        {
            Debug.LogWarning($"[Avatar] 頭貼解碼失敗：{e.Message}");
            return null;
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