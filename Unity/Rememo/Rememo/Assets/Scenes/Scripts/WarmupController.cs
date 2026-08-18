using UnityEngine;
using UnityEngine.UI;
using UnityEngine.SceneManagement;
using System.Collections;

public class WarmupController : MonoBehaviour
{
    [Header("UI 元件")]
    public Image statusBadge;

    [Header("圖片")]
    public Sprite detectingSprite;  // 設備偵測中
    public Sprite successSprite;    // 設備偵測成功

    private KinectCalibrationManager calibrationManager;

    void Start()
    {
        calibrationManager = Object.FindFirstObjectByType<KinectCalibrationManager>();
        StartCoroutine(WaitForCalibration());
    }

    IEnumerator WaitForCalibration()
    {
        while (calibrationManager != null && !calibrationManager.IsCalibrated)
        {
            statusBadge.sprite = detectingSprite;
            yield return null;
        }

        // 校正完成。等治療師端按下「啟動療程」、後端生成第一回合內容這段真正花時間的
        // 過程交給 InstructionScene 的進度條呈現，這裡校正一完成就直接過去，
        // 不再讓長者端停在 WarmupScene 乾等。
        statusBadge.sprite = successSprite;
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
