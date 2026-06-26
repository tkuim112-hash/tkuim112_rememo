using UnityEngine;           // Unity 核心
using UnityEngine.UI;        // UI 元件（Button 等）
using UnityEngine.SceneManagement; // 場景切換
using TMPro;                 // TextMeshPro 文字元件
using System.Collections;   // IEnumerator / Coroutine 支援

public class GameController : MonoBehaviour
{
    [Header("回合設定")]
    public int totalRounds = 3;          // 總回合數
    public static int currentRound = 1; // 目前回合（static 讓其他 Script 也能讀取）

    [Header("UI 元件")]
    public TMP_Text roundNumber;    // 顯示回合數字的文字
    public TMP_Text roundTitle;     // 顯示「第X回合」的文字
    public TMP_Text roundSub;       // 顯示「ROUND X / 3」的副標文字
    public Button submitButton;     // 送出按鈕
    public TMP_Text aiText;         // 顯示 AI 回應的文字
    public TMP_Text inputText;      // 使用者輸入欄位
    public GameObject loadingSpinner; // 載入中轉圈動畫物件

    private string[] roundNames = { "第一回合", "第二回合", "第三回合" }; // 各回合標題
    private string placeholderText = "想到什麼就說什麼，按下麥克風可以用說的…"; // 輸入欄預設提示文字

    // 模擬 AI 回應內容（正式版應替換為真實 API 呼叫）
    private string[] fakeResponses = {
        "美麗阿姨，這幅畫裡的紅瓦房跟老家好像！看著看著，仿佛又聽見那個夏天熱鬧的蟬鳴聲。",
        "阿公，您說的紡織廠讓我想起了那個年代，大家一起打拼的日子真的很珍貴。",
        "聽您說起淡水的往事，那個年代的台灣真的很美，充滿了人情味。",
    };

    void Start()
    {
        submitButton.onClick.AddListener(OnSubmit); // 綁定送出按鈕事件
        UpdateRoundBadge();                         // 初始化回合顯示
        loadingSpinner.SetActive(false);            // 預設隱藏載入動畫
    }

    void OnSubmit()
    {
        StartCoroutine(ProcessSubmit()); // 啟動送出流程協程
    }

    IEnumerator ProcessSubmit()
    {
        // 第三回合送出後直接跳場景
        if (currentRound >= totalRounds)
        {
            currentRound = 1;
            PlayerPrefs.SetString("NextScene", "ShareScene");
            SceneManager.LoadScene("LoadingScene");
            yield break;
        }

        // 清空輸入欄，還原成提示文字與灰色
        inputText.text = placeholderText;
        inputText.color = new Color(0.67f, 0.67f, 0.67f, 1f);

        // 隱藏 AI 文字，顯示載入動畫
        aiText.gameObject.SetActive(false);
        loadingSpinner.SetActive(true);

        // 模擬 AI 處理等待 2 秒
        yield return new WaitForSeconds(2f);

        // 隱藏載入動畫，顯示 AI 回應泡泡
        loadingSpinner.SetActive(false);
        aiText.gameObject.SetActive(true);

        // 根據目前回合取得對應 AI 回應文字
        aiText.text = fakeResponses[currentRound % fakeResponses.Length];

        currentRound++;        // 前進到下一回合
        UpdateRoundBadge();    // 更新回合顯示 UI
    }

    void UpdateRoundBadge()
    {
        roundNumber.text = currentRound.ToString();          // 更新數字
        roundTitle.text = roundNames[currentRound - 1];     // 更新回合標題
        roundSub.text = $"ROUND {currentRound} / {totalRounds}"; // 更新副標
    }
}
