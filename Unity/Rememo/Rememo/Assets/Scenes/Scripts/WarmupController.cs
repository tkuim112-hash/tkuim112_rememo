using UnityEngine;
using UnityEngine.UI;
using UnityEngine.SceneManagement;
using System.Collections;

public class WarmupController : MonoBehaviour
{
    [Header("UI 元件")]
    public Button startButton;
    public Image statusBadge;

    [Header("圖片")]
    public Sprite detectingSprite;  // 設備偵測中
    public Sprite successSprite;    // 設備偵測成功

    private KinectCalibrationManager calibrationManager;

    void Start()
    {
        startButton.interactable = false;
        startButton.onClick.AddListener(OnStart);

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
        startButton.interactable = true;
        OnStart();
    }

    void OnStart()
    {
        PlayerPrefs.SetString("NextScene", "GameScene-1");
        SceneManager.LoadScene("InstructionScene");
    }
}
