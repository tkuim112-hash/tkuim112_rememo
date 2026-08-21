using UnityEngine;
using UnityEngine.UI;
using UnityEngine.SceneManagement;
using UnityEngine.Networking;
using System.Collections;

/// <summary>
/// WarmupScene 校正完成、治療師端一按下「啟動療程」就馬上切來這裡（不等後端生成完畢）。
/// 真正花時間的是後端：LLM 主題分類、RAG 記憶檢索、TTS 合成，這段等待在這裡用進度條
/// 動畫呈現，不讓長者端停在 WarmupScene 乾等。等內容真的生成完畢（/status 回
/// started=true），再打一次 /session/start 把結果拿回來（命中後端快取，幾乎即時）
/// 交給 GameScene，GameScene 進場就能直接顯示，不用再自己打一次 API、轉一次自己的 spinner。
/// </summary>
public class InstructionController : MonoBehaviour
{
    [Header("後端設定")]
    public string backendUrl = "https://api.re-memo.com";
    public string userId = "user_001";
    public string sessionId = "sess_001";
    [Tooltip("等後端生成完畢，每隔幾秒 poll 一次 /status")]
    public float contentPollInterval = 2f;

    [Header("UI 元件")]
    public Image progressBar;

    [Header("進度條動畫參數")]
    [Tooltip("等後端生成內容的期間，進度條先爬到這個比例，剩下留給內容真的生成完畢那一刻")]
    [Range(0f, 1f)] public float waitingCeiling = 0.5f;
    [Tooltip("爬到 waitingCeiling 大約要花幾秒（爬升會隨等待時間趨緩，不會卡住不動）")]
    public float waitingRiseTime = 8f;
    [Tooltip("內容準備好之後，進度條至少要在畫面上多停留幾秒再切場景，避免一閃而過")]
    public float minHoldTime = 0.4f;

    private string nextScene = "GameScene-1";
    private float elapsedWaiting = 0f;

    void Start()
    {
        string selectedPatientId = PlayerPrefs.GetString("SelectedPatientId", "");
        if (!string.IsNullOrEmpty(selectedPatientId)) userId = selectedPatientId;

        string sharedSessionId = AuthSession.SessionId ?? "";
        if (!string.IsNullOrEmpty(sharedSessionId)) sessionId = sharedSessionId;

        if (PlayerPrefs.HasKey("NextScene"))
            nextScene = PlayerPrefs.GetString("NextScene");

        progressBar.fillAmount = 0f;
        StartCoroutine(RunLoadingFlow());
    }

    IEnumerator RunLoadingFlow()
    {
        bool hasSession = !string.IsNullOrEmpty(sessionId);

        if (hasSession)
        {
            yield return StartCoroutine(WaitForContentReady());
            yield return StartCoroutine(FetchFirstRound());
        }
        // sessionId 拿不到（換取 pending session 失敗、離線 demo）就跳過等待與預抓，
        // 直接放行讓 GameScene 進場時照舊自己打一次 API，不讓這個環節卡住展示。

        yield return StartCoroutine(FillToComplete());
        SceneManager.LoadScene(nextScene);
    }

    IEnumerator WaitForContentReady()
    {
        var wait = new WaitForSeconds(contentPollInterval);
        while (true)
        {
            bool started = false;
            yield return StartCoroutine(SessionService.FetchStatus(
                backendUrl,
                sessionId,
                result => started = result,
                error => Debug.LogWarning(error)
            ));
            if (started) yield break;

            elapsedWaiting += contentPollInterval;
            float target = waitingCeiling * (1f - Mathf.Exp(-elapsedWaiting / waitingRiseTime));
            progressBar.fillAmount = Mathf.Max(progressBar.fillAmount, target);
            yield return wait;
        }
    }

    IEnumerator FetchFirstRound()
    {
        string url = $"{backendUrl}/session/start?user_id={userId}&session_id={sessionId}";
        using var req = UnityWebRequest.PostWwwForm(url, "");
        AuthService.AttachAuthHeader(req);

        var op = req.SendWebRequest();
        while (!op.isDone)
        {
            // 這一步命中後端快取、幾乎即時回來，用來銜接前一階段的爬升終點到接近滿格。
            progressBar.fillAmount = Mathf.Max(progressBar.fillAmount, waitingCeiling + (1f - waitingCeiling) * 0.5f);
            yield return null;
        }

        if (req.result == UnityWebRequest.Result.Success)
            PendingSessionStart.Response = JsonUtility.FromJson<StartRoundResponse>(req.downloadHandler.text);
        else
            Debug.LogWarning($"[Instruction] 第一回合資料預先載入失敗，交給 GameScene 自行重試: {req.error}");
    }

    IEnumerator FillToComplete()
    {
        while (progressBar.fillAmount < 0.999f)
        {
            progressBar.fillAmount = Mathf.MoveTowards(progressBar.fillAmount, 1f, Time.deltaTime * 2f);
            yield return null;
        }
        progressBar.fillAmount = 1f;
        yield return new WaitForSeconds(minHoldTime);
    }
}
