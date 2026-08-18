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
    [Tooltip("頭/雙肩/雙髖這幾個關鍵關節，至少要在這個比例的影格裡被追蹤到，才代表整個人都在鏡頭範圍內")]
    public float minKeyJointTrackedRatio = 0.8f;
    public bool IsCalibrated { get; private set; }

    static readonly string[] KeyJointsForCompleteness =
    {
        "Head", "ShoulderLeft", "ShoulderRight", "HipLeft", "HipRight",
    };

    [Header("UI 元件")]
    public Slider progressBar;
    public Image statusIndicator;
    public Sprite spriteDetecting;
    public Sprite spriteSuccess;

    [Header("WebSocket 設定")]
    public string calibrationUrl = "wss://api.re-memo.com/ws/calibration";

    private readonly Color COLOR_ORANGE = new Color(1f, 0.6f, 0f);
    private readonly Color COLOR_GREEN = new Color(0.2f, 0.8f, 0.2f);

    private bool isCalibrating = false;
    private float calibrationTimer = 0f;

    private List<Dictionary<string, float[]>> skeletonBuffer = new List<Dictionary<string, float[]>>();
    private List<float> happyBuffer = new List<float>();
    private List<float> lookingAwayBuffer = new List<float>();
    private List<float> mouthMovedBuffer = new List<float>();
    private List<float> _pitchVarBuffer  = new List<float>();

    // ── 新增：骨架幾何緩衝 ─────────────────────────────
    private List<float> _spineYBuffer = new List<float>();
    private List<float> _headYBuffer = new List<float>();
    private List<float> _shoulderLXBuffer = new List<float>();
    private List<float> _shoulderRXBuffer = new List<float>();
    private List<float> _spineZBuffer = new List<float>();
    // ───────────────────────────────────────────────────

    [Header("感測器參考")]
    [Tooltip("拖入場景中的 KinectAudioSender；校正期間量測音高基準")]
    public KinectAudioSender audioSender;

    private WebSocket ws;
    private KinectManager kinectManager;
    private KinectSensorSender sensorSender;

    void Start()
    {
        kinectManager = KinectManager.Instance;
        sensorSender = GetComponent<KinectSensorSender>();

        if (audioSender == null)
            Debug.LogWarning("[Calibration] audioSender 未設定，pitchVarianceBaseline 將為 0（個人化音高門檻無效）");

        SetStatus(false);

        // session_id 必須在 PlayerPrefs 中（由前一個 Scene 建立），才能讓後端把校正基準與本次療程綁定
        string sessionId = PlayerPrefs.GetString("session_id", "");
        if (string.IsNullOrEmpty(sessionId))
            Debug.LogWarning("[Calibration] PlayerPrefs 中無 session_id，校正基準將無法存入 Redis");
        string wsUrl = string.IsNullOrEmpty(sessionId)
            ? calibrationUrl
            : $"{calibrationUrl}?session_id={sessionId}";
        wsUrl = AuthService.AppendToken(wsUrl);

        ws = new WebSocket(wsUrl);
        ws.SslConfiguration.EnabledSslProtocols = System.Security.Authentication.SslProtocols.Tls12;
        ws.OnOpen += (s, e) => Debug.Log("[Calibration WS] 已連線");
        ws.OnError += (s, e) => Debug.LogError($"[Calibration WS] 錯誤: {e.Message}");
        // 後端存成功/失敗都會回一個 JSON（見 ws_calibration.py）；SendCalibrationData() 裡的
        // SendAsync 只代表送出動作沒出錯，不代表後端真的收到、存進 Redis，這裡才是送達確認。
        ws.OnMessage += (s, e) => Debug.Log($"[Calibration WS] 後端回應: {e.Data}");
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

        // CheckStability 只看 SpineBase 這一點晃不晃，就算只有半個人在鏡頭裡、
        // 其他關節整場都沒被追蹤到，只要 SpineBase 穩定就會判定通過。
        // CheckJointCompleteness 補上「人有沒有完整在鏡頭範圍內」的檢查。
        bool isStable = CheckStability() && CheckJointCompleteness();

        if (isStable)
        {
            IsCalibrated = true;
            SetStatus(true);
            ApplyCursorRemapping(); // ── 新增
            SendCalibrationData();
            Debug.Log("[Calibration] 校正完成（見 WarmupController）");
            // 場景切換交給 WarmupController：校正一完成就直接切去 InstructionScene，
            // 「等治療師端按下啟動療程、後端生成第一回合內容」這段改由 InstructionScene
            // 自己 poll 後端狀態並顯示進度條動畫，這裡不再自行 sleep 後跳場景。
        }
        else
        {
            Debug.Log("[Calibration] 數據不穩定或關節追蹤不完整，重新校正");
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
        if (sensorSender == null) return;

        happyBuffer.Add(sensorSender.LastHappy);
        lookingAwayBuffer.Add(sensorSender.LastLookingAway);
        mouthMovedBuffer.Add(sensorSender.LastMouthMoved);

        // 靜音期間的音高變異值不具代表性，僅在有聲音時蒐集
        if (audioSender != null && audioSender.CurrentAudioRms > 0.005f)
            _pitchVarBuffer.Add(audioSender.CurrentPitchVariance);
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

    /// <summary>
    /// CheckStability 只驗證 SpineBase 有沒有晃，沒驗證整個人是不是都在鏡頭範圍內——
    /// 只要 SpineBase 追蹤得到又夠穩，就算頭、肩膀、髖部整場都沒被追蹤到也會判定通過。
    /// 這裡另外檢查幾個關鍵關節，要求在夠高比例的影格裡都有被追蹤到，
    /// 確保「校正完成」代表的是完整的人，不是只有半個人在畫面裡剛好沒動。
    /// </summary>
    bool CheckJointCompleteness()
    {
        if (skeletonBuffer.Count == 0) return false;

        foreach (string jointName in KeyJointsForCompleteness)
        {
            int trackedCount = 0;
            foreach (var frame in skeletonBuffer)
            {
                if (frame.ContainsKey(jointName)) trackedCount++;
            }

            float ratio = (float)trackedCount / skeletonBuffer.Count;
            if (ratio < minKeyJointTrackedRatio)
            {
                Debug.Log($"[Calibration] 關節 {jointName} 追蹤率不足：{ratio:P0}（需要 {minKeyJointTrackedRatio:P0}），可能只有部分身體在鏡頭範圍內");
                return false;
            }
        }

        return true;
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
        _pitchVarBuffer.Clear();
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
            pitchVarianceBaseline = Average(_pitchVarBuffer),
            jointKeys = new List<string>(baselineJoints.Keys).ToArray(),
            jointX = GetAxis(baselineJoints, 0),
            jointY = GetAxis(baselineJoints, 1),
            jointZ = GetAxis(baselineJoints, 2)
        };

        ws.SendAsync(JsonUtility.ToJson(payload), null);
        Debug.Log("[Calibration] 基準值已送出（送達確認見上面的 [Calibration WS] 後端回應）");
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
    public float pitchVarianceBaseline;
    public string[] jointKeys;
    public float[] jointX;
    public float[] jointY;
    public float[] jointZ;
}