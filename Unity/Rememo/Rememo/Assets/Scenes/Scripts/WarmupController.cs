using UnityEngine;
using UnityEngine.UI;
using UnityEngine.SceneManagement;
using System.Collections;

public class WarmupController : MonoBehaviour
{
    [Header("後端設定")]
    public string backendUrl = "https://api.re-memo.com";
    [Tooltip("等治療師啟動療程時，每隔幾秒 poll 一次後端狀態")]
    public float therapistPollInterval = 2f;

    [Header("UI 元件")]
    public Button startButton;
    public Image statusBadge;

    [Header("圖片")]
    public Sprite detectingSprite;  // 設備偵測中
    public Sprite successSprite;    // 設備偵測成功

    private KinectCalibrationManager calibrationManager;

    void Start()
    {
        // 預設禁用開始按鈕，等校正完成且治療師端按下「啟動療程」才放行
        startButton.interactable = false;
        startButton.onClick.AddListener(OnStart);

        calibrationManager = Object.FindFirstObjectByType<KinectCalibrationManager>();
        StartCoroutine(WaitForCalibrationAndTherapistStart());
    }

    IEnumerator WaitForCalibrationAndTherapistStart()
    {
        // 等待校正完成
        while (calibrationManager != null && !calibrationManager.IsCalibrated)
        {
            statusBadge.sprite = detectingSprite;
            yield return null;
        }

        // 校正完成
        statusBadge.sprite = successSprite;

        // 等待治療師控制端確認：poll 後端直到治療師按下「啟動療程」（/session/start 已被呼叫）。
        // sessionId 若拿不到（例如換取 pending session 失敗、離線 demo），
        // 就沿用舊行為直接放行，不讓這個環節卡住展示。
        string sessionId = PlayerPrefs.GetString("session_id", "");
        var wait = new WaitForSeconds(therapistPollInterval);
        while (!string.IsNullOrEmpty(sessionId))
        {
            bool started = false;
            yield return StartCoroutine(SessionService.FetchStatus(
                backendUrl,
                sessionId,
                result => started = result,
                error => Debug.LogWarning(error)
            ));
            if (started) break;
            yield return wait;
        }

        startButton.interactable = true;
    }

    void OnStart()
    {
        PlayerPrefs.SetString("NextScene", "GameScene-1");
        SceneManager.LoadScene("LoadingScene");
    }
}