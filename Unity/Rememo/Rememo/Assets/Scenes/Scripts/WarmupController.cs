using UnityEngine;
using UnityEngine.UI;
using UnityEngine.SceneManagement;
using System.Collections;

public class WarmupController : MonoBehaviour
{
    [Header("後端設定")]
    public string backendUrl = "https://api.re-memo.com";
    [Tooltip("校正完成後，等治療師按下「啟動療程」，每隔幾秒 poll 一次後端狀態")]
    public float therapistPollInterval = 0.5f;

    [Header("UI 元件")]
    public Image statusBadge;

    [Header("圖片")]
    public Sprite detectingSprite;  // 設備偵測中
    public Sprite successSprite;    // 設備偵測成功

    private KinectCalibrationManager calibrationManager;
    private string sessionId;

    void Start()
    {
        sessionId = PlayerPrefs.GetString("session_id", "");
        calibrationManager = Object.FindFirstObjectByType<KinectCalibrationManager>();
        StartCoroutine(WaitForCalibrationThenTherapistStart());
    }

    IEnumerator WaitForCalibrationThenTherapistStart()
    {
        while (calibrationManager != null && !calibrationManager.IsCalibrated)
        {
            statusBadge.sprite = detectingSprite;
            yield return null;
        }

        statusBadge.sprite = successSprite;

        // 校正完成，等治療師端按下「啟動療程」（後端 /session/start 一被呼叫就馬上標記
        // requested=true，不等 LLM 分類／RAG 檢索／TTS 合成跑完）就立刻切去
        // InstructionScene——真正耗時的生成過程改到說明頁用進度條呈現，不讓長者
        // 停在 WarmupScene 乾等。sessionId 拿不到（離線 demo、換取 pending session
        // 失敗）就沿用舊行為直接放行，不讓這個環節卡住展示。
        if (!string.IsNullOrEmpty(sessionId))
        {
            var wait = new WaitForSeconds(therapistPollInterval);
            bool requested = false;
            while (!requested)
            {
                yield return StartCoroutine(SessionService.FetchRequested(
                    backendUrl,
                    sessionId,
                    result => requested = result,
                    error => Debug.LogWarning(error)
                ));
                if (requested) break;
                yield return wait;
            }
        }

        OnStart();
    }

    void OnStart()
    {
        // 每次到這裡代表一場全新的療程要開始。GameController.currentRound
        // 是 static 欄位，只有正常跑完三回合觸發 end_session 才會重置回1
        // （見 GameController.cs:547）；上一場如果中途結束（治療師提前按
        // 結束、斷線等），這個值會殘留在2或3，導致這場新療程的 GameScene
        // 誤判成「接著上次沒跑完的回合」，跳過 round 1 該有的主題分類／
        // 開場模板生成邏輯，直接打 /session/round 問出跟這場主題無關的
        // 問題（2026-08-19 稽核發現）。PendingSessionStart.Response 也一併
        // 清掉，避免殘留的舊回合1資料被下一場療程誤用。
        GameController.currentRound = 1;
        PendingSessionStart.Response = null;
        PlayerPrefs.SetString("NextScene", "GameScene-1");
        SceneManager.LoadScene("InstructionScene");
    }
}
