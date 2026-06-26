using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;
using WebSocketSharp;

public class KinectCalibrationManager : MonoBehaviour
{
    [Header("校正設定")]
    public float calibrationDuration = 15f;
    public float stabilityThreshold = 0.05f;
    public bool IsCalibrated { get; private set; }

    [Header("UI 元件")]
    public Slider progressBar;
    public Image statusIndicator;
    public Sprite spriteDetecting;
    public Sprite spriteSuccess;

    [Header("WebSocket 設定")]
    public string calibrationUrl = "ws://localhost:8000/ws/calibration";

    private readonly Color COLOR_ORANGE = new Color(1f, 0.6f, 0f);
    private readonly Color COLOR_GREEN = new Color(0.2f, 0.8f, 0.2f);

    private bool isCalibrating = false;
    private float calibrationTimer = 0f;

    private List<Dictionary<string, float[]>> skeletonBuffer = new List<Dictionary<string, float[]>>();
    private List<float> happyBuffer = new List<float>();
    private List<float> lookingAwayBuffer = new List<float>();
    private List<float> mouthMovedBuffer = new List<float>();

    // ── 新增：骨架幾何緩衝 ─────────────────────────────
    private List<float> _spineYBuffer = new List<float>();
    private List<float> _headYBuffer = new List<float>();
    private List<float> _shoulderLXBuffer = new List<float>();
    private List<float> _shoulderRXBuffer = new List<float>();
    private List<float> _spineZBuffer = new List<float>();
    // ───────────────────────────────────────────────────

    private WebSocket ws;
    private KinectManager kinectManager;
    private KinectEmotionSender emotionSender;

    void Start()
    {
        kinectManager = KinectManager.Instance;
        emotionSender = GetComponent<KinectEmotionSender>();

        SetStatus(false);

        ws = new WebSocket(calibrationUrl);
        ws.OnOpen += (s, e) => Debug.Log("[Calibration WS] 已連線");
        ws.OnError += (s, e) => Debug.LogError($"[Calibration WS] 錯誤: {e.Message}");
        ws.ConnectAsync();

        StartCoroutine(CalibrationRoutine());
    }

    IEnumerator CalibrationRoutine()
    {
        isCalibrating = true;
        calibrationTimer = 0f;
        Debug.Log("[Calibration] 等待使用者進入鏡頭...");
        while (true)
        {
            if (kinectManager != null &&
                kinectManager.IsInitialized() &&
                kinectManager.GetPrimaryUserID() != 0)
                break;

            yield return null;
        }
        Debug.Log("[Calibration] 使用者已就位，開始蒐集");

        Debug.Log("[Calibration] 開始蒐集基準值");

        while (calibrationTimer < calibrationDuration)
        {
            calibrationTimer += Time.deltaTime;

            if (progressBar != null)
                progressBar.value = calibrationTimer / calibrationDuration;

            CollectSkeletonData();
            CollectEmotionData();

            yield return null;
        }

        bool isStable = CheckStability();

        if (isStable)
        {
            IsCalibrated = true;
            SetStatus(true);
            ApplyCursorRemapping(); // ── 新增
            SendCalibrationData();
            Debug.Log("[Calibration] 校正完成，等待治療師確認");
            // ── 暫時測試用，之後改成等治療師網頁觸發 ──
            yield return new WaitForSeconds(2f); // 等 2 秒讓你看到校正完成
            UnityEngine.SceneManagement.SceneManager.LoadScene("GameScene-1");
        }
        else
        {
            Debug.Log("[Calibration] 數據不穩定，重新校正");
            skeletonBuffer.Clear();
            happyBuffer.Clear();
            lookingAwayBuffer.Clear();
            mouthMovedBuffer.Clear();
            ClearGeometryBuffers(); // ── 新增
            StartCoroutine(CalibrationRoutine());
        }
    }

    void CollectSkeletonData()
    {
        if (kinectManager == null || !kinectManager.IsInitialized()) return;

        long userId = kinectManager.GetPrimaryUserID();
        if (userId == 0) return;

        var joints = new Dictionary<string, float[]>();
        for (int j = 0; j < 25; j++)
        {
            if (kinectManager.IsJointTracked(userId, j))
            {
                Vector3 pos = kinectManager.GetJointPosition(userId, j);
                string jointName = ((KinectInterop.JointType)j).ToString();
                joints[jointName] = new float[] { pos.x, pos.y, pos.z };

                // ── 新增：同步蒐集推算用關節 ──────────────
                switch ((KinectInterop.JointType)j)
                {
                    case KinectInterop.JointType.SpineBase:
                        _spineYBuffer.Add(pos.y);
                        _spineZBuffer.Add(pos.z);
                        break;
                    case KinectInterop.JointType.Head:
                        _headYBuffer.Add(pos.y);
                        break;
                    case KinectInterop.JointType.ShoulderLeft:
                        _shoulderLXBuffer.Add(pos.x);
                        break;
                    case KinectInterop.JointType.ShoulderRight:
                        _shoulderRXBuffer.Add(pos.x);
                        break;
                }
                // ──────────────────────────────────────────
            }
        }

        if (joints.Count > 0)
            skeletonBuffer.Add(joints);
    }

    void CollectEmotionData()
    {
        if (emotionSender == null) return;

        happyBuffer.Add(emotionSender.LastHappy);
        lookingAwayBuffer.Add(emotionSender.LastLookingAway);
        mouthMovedBuffer.Add(emotionSender.LastMouthMoved);
    }

    bool CheckStability()
    {
        if (skeletonBuffer.Count < 10) return false;

        var spinePositions = new List<Vector3>();
        foreach (var frame in skeletonBuffer)
        {
            if (frame.ContainsKey("SpineBase"))
            {
                var pos = frame["SpineBase"];
                spinePositions.Add(new Vector3(pos[0], pos[1], pos[2]));
            }
        }

        if (spinePositions.Count < 10) return false;

        Vector3 avg = Vector3.zero;
        foreach (var p in spinePositions) avg += p;
        avg /= spinePositions.Count;

        float variance = 0f;
        foreach (var p in spinePositions)
            variance += (p - avg).sqrMagnitude;
        variance /= spinePositions.Count;
        float stdDev = Mathf.Sqrt(variance);

        Debug.Log($"[Calibration] 骨架穩定度: {stdDev}（閾值: {stabilityThreshold}）");
        return stdDev < stabilityThreshold;
    }

    // ── 新增：游標映射推算，結果寫入 CalibrationData ───
    void ApplyCursorRemapping()
    {
        if (_spineYBuffer.Count == 0 || _headYBuffer.Count == 0) return;

        float spineY = Average(_spineYBuffer);
        float headY = Average(_headYBuffer);
        float shoulderLX = Average(_shoulderLXBuffer);
        float shoulderRX = Average(_shoulderRXBuffer);
        float avgZ = Average(_spineZBuffer);

        float bodyHeight = headY - spineY;

        // ── 手部實際操作 Y 範圍（坐姿）────────────────────────
        // 手最低約在脊椎高度（放腿上），最高約在肩膀高度（bodyHeight * 0.7）
        CalibrationData.WorldYMin = spineY - 0.05f;                      // 手放腿上時
        CalibrationData.WorldYMax = spineY + bodyHeight * 0.75f + 0.10f; // 手抬到約肩膀

        // X 範圍不變
        CalibrationData.WorldXMin = shoulderLX - 0.20f;
        CalibrationData.WorldXMax = shoulderRX + 0.20f;

        CalibrationData.WorldZ = avgZ;
        CalibrationData.IsCalibrated = true;

        Debug.Log($"[Calibration] 坐姿座標範圍 → " +
                  $"X:{CalibrationData.WorldXMin:F2}~{CalibrationData.WorldXMax:F2} " +
                  $"Y:{CalibrationData.WorldYMin:F2}~{CalibrationData.WorldYMax:F2} " +
                  $"Z:{CalibrationData.WorldZ:F2}");
    }

    void ClearGeometryBuffers()
    {
        _spineYBuffer.Clear();
        _headYBuffer.Clear();
        _shoulderLXBuffer.Clear();
        _shoulderRXBuffer.Clear();
        _spineZBuffer.Clear();
    }
    // ───────────────────────────────────────────────────

    void SendCalibrationData()
    {
        if (ws == null || ws.ReadyState != WebSocketState.Open) return;

        var baselineJoints = new Dictionary<string, float[]>();
        var jointSums = new Dictionary<string, float[]>();
        var jointCounts = new Dictionary<string, int>();

        foreach (var frame in skeletonBuffer)
        {
            foreach (var kvp in frame)
            {
                if (!jointSums.ContainsKey(kvp.Key))
                {
                    jointSums[kvp.Key] = new float[] { 0, 0, 0 };
                    jointCounts[kvp.Key] = 0;
                }
                jointSums[kvp.Key][0] += kvp.Value[0];
                jointSums[kvp.Key][1] += kvp.Value[1];
                jointSums[kvp.Key][2] += kvp.Value[2];
                jointCounts[kvp.Key]++;
            }
        }

        foreach (var kvp in jointSums)
        {
            int count = jointCounts[kvp.Key];
            baselineJoints[kvp.Key] = new float[]
            {
                kvp.Value[0] / count,
                kvp.Value[1] / count,
                kvp.Value[2] / count
            };
        }

        var payload = new CalibrationPayload
        {
            type = "calibration",
            duration = calibrationDuration,
            happyBaseline = Average(happyBuffer),
            lookingAwayBaseline = Average(lookingAwayBuffer),
            mouthMovedBaseline = Average(mouthMovedBuffer),
            jointKeys = new List<string>(baselineJoints.Keys).ToArray(),
            jointX = GetAxis(baselineJoints, 0),
            jointY = GetAxis(baselineJoints, 1),
            jointZ = GetAxis(baselineJoints, 2)
        };

        ws.SendAsync(JsonUtility.ToJson(payload), null);
        Debug.Log("[Calibration] 基準值已送出");
    }

    float Average(List<float> list)
    {
        if (list.Count == 0) return 0f;
        float sum = 0f;
        foreach (var v in list) sum += v;
        return sum / list.Count;
    }

    float[] GetAxis(Dictionary<string, float[]> joints, int axis)
    {
        var result = new float[joints.Count];
        int i = 0;
        foreach (var v in joints.Values)
            result[i++] = v[axis];
        return result;
    }

    void SetStatus(bool calibrated)
    {
        if (statusIndicator != null)
            statusIndicator.sprite = calibrated ? spriteSuccess : spriteDetecting;
    }

    void OnDestroy() => ws?.Close();
}

[System.Serializable]
public class CalibrationPayload
{
    public string type;
    public float duration;
    public float happyBaseline;
    public float lookingAwayBaseline;
    public float mouthMovedBaseline;
    public string[] jointKeys;
    public float[] jointX;
    public float[] jointY;
    public float[] jointZ;
}