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
        // 預設禁用開始按鈕，等校正完成
        startButton.interactable = false;
        startButton.onClick.AddListener(OnStart);

        calibrationManager = Object.FindFirstObjectByType<KinectCalibrationManager>();
        StartCoroutine(WaitForCalibration());
    }

    IEnumerator WaitForCalibration()
    {
        // 等待校正完成
        while (calibrationManager != null && !calibrationManager.IsCalibrated)
        {
            statusBadge.sprite = detectingSprite;
            yield return null;
        }

        // 校正完成
        statusBadge.sprite = successSprite;

        // 等待治療師控制端確認（開始按鈕變可用）
        startButton.interactable = true;
    }

    void OnStart()
    {
        PlayerPrefs.SetString("NextScene", "GameScene-1");
        SceneManager.LoadScene("LoadingScene");
    }
}