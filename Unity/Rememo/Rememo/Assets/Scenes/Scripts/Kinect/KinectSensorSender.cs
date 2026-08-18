using System;
using System.Collections;
using System.Collections.Generic;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;
using Windows.Kinect;
using Microsoft.Kinect.Face;

/// <summary>
/// 感測資料蒐集器：讀取臉部特徵、骨架量測值、麥克風音量，
/// 組成原始 payload 送給後端。情緒分類邏輯完全在後端執行。
/// </summary>
public class KinectSensorSender : MonoBehaviour
{
    // 供其他腳本讀取原始臉部數值
    public float LastHappy       { get; private set; }
    public float LastLookingAway { get; private set; }
    public float LastMouthMoved  { get; private set; }

    [Header("後端設定")]
    public string backendUrl   = "https://api.re-memo.com";
    public float  sendInterval = 2f;

    [Header("外部參考")]
    public GameController    gameController;
    public KinectAudioSender audioSender;

    [Header("反應時間偵測")]
    [Tooltip("麥克風 RMS 超過此值視為長者開始說話（用於計算反應時間）。預設 0.015")]
    public float audioSpeechThreshold = 0.015f;

    // Kinect Face
    private KinectSensor    sensor;
    private BodyFrameReader bodyReader;
    private FaceFrameSource faceSource;
    private FaceFrameReader faceReader;
    private Body[]          bodies;

    // Kinect Skeleton
    private KinectManager kinectManager;

    // Timer
    private float sendTimer = 0f;
    private float swayTimer = 0f;
    private const float SWAY_SAMPLE_INTERVAL = 0.25f;  // 每 0.25s 取一個 SpineBase 樣本

    // Body Sway（SpineBase 6 秒滑動視窗）
    private readonly Queue<Vector3> swayHistory = new Queue<Vector3>();
    private const int SWAY_HISTORY = 24;  // 24 × 0.25s = 6s

    // 反應時間
    private float questionAskedTime = -1f;
    private bool  responseTimeSent  = false;

    private const int BODY_COUNT = 6;

    // HandTip 速度（B 階段）
    private float   _latestHandTipVelocity = 0f;
    private Vector3 _prevHandTipL          = Vector3.zero;
    private Vector3 _prevHandTipR          = Vector3.zero;
    private bool    _prevHandTipValid      = false;

    // ════════════════════════════════════════════════════════════════

    void Start()
    {
        kinectManager = KinectManager.Instance;
        sensor = KinectSensor.GetDefault();
        if (sensor == null) { Debug.LogError("[Emotion] 找不到 Kinect 感測器"); return; }

        var features = FaceFrameFeatures.Happy
                     | FaceFrameFeatures.LeftEyeClosed
                     | FaceFrameFeatures.RightEyeClosed
                     | FaceFrameFeatures.LookingAway
                     | FaceFrameFeatures.MouthOpen
                     | FaceFrameFeatures.MouthMoved;

        bodyReader = sensor.BodyFrameSource.OpenReader();
        bodies     = new Body[BODY_COUNT];
        faceSource = FaceFrameSource.Create(sensor, 0, features);
        faceReader = faceSource.OpenReader();
        if (!sensor.IsOpen) sensor.Open();
    }

    /// <summary>GameController 在 TTS 播完、顯示問題後呼叫，啟動反應時間計時。</summary>
    public void OnQuestionAsked()
    {
        questionAskedTime = Time.realtimeSinceStartup;
        responseTimeSent  = false;
    }

    void Update()
    {
        long userId = (kinectManager != null && kinectManager.IsInitialized())
            ? kinectManager.GetPrimaryUserID() : 0;

        // 高頻：取樣骨架供晃動計算
        swayTimer += Time.deltaTime;
        if (swayTimer >= SWAY_SAMPLE_INTERVAL)
        {
            swayTimer = 0f;
            if (userId != 0) SampleSpineForSway(userId);
        }

        // 低頻：收集所有訊號並送出
        sendTimer += Time.deltaTime;
        if (sendTimer < sendInterval) return;
        sendTimer = 0f;

        CollectAndSend(userId);
    }

    // ════════════ 骨架晃動取樣 ════════════════════════════════════════

    void SampleSpineForSway(long userId)
    {
        if (!kinectManager.IsJointTracked(userId, (int)KinectInterop.JointType.SpineBase)) return;
        Vector3 pos = kinectManager.GetJointPosition(userId, (int)KinectInterop.JointType.SpineBase);
        swayHistory.Enqueue(pos);
        if (swayHistory.Count > SWAY_HISTORY) swayHistory.Dequeue();
        SampleHandTipVelocity(userId);
    }

    void SampleHandTipVelocity(long userId)
    {
        var posL = GetJoint(userId, KinectInterop.JointType.HandTipLeft);
        var posR = GetJoint(userId, KinectInterop.JointType.HandTipRight);
        if (!posL.HasValue || !posR.HasValue) { _prevHandTipValid = false; return; }
        if (_prevHandTipValid)
        {
            float vL = Vector3.Distance(posL.Value, _prevHandTipL) / SWAY_SAMPLE_INTERVAL;
            float vR = Vector3.Distance(posR.Value, _prevHandTipR) / SWAY_SAMPLE_INTERVAL;
            _latestHandTipVelocity = Mathf.Max(vL, vR);
        }
        _prevHandTipL     = posL.Value;
        _prevHandTipR     = posR.Value;
        _prevHandTipValid = true;
    }

    // ════════════ 主蒐集流程 ═════════════════════════════════════════

    void CollectAndSend(long userId)
    {
        // ── 更新 Face Tracking ID ──────────────────────────────────
        using (var frame = bodyReader?.AcquireLatestFrame())
        {
            if (frame != null)
            {
                frame.GetAndRefreshBodyData(bodies);
                if (!faceSource.IsTrackingIdValid)
                    foreach (var body in bodies)
                        if (body != null && body.IsTracked) { faceSource.TrackingId = body.TrackingId; break; }
            }
        }

        using (var faceFrame = faceReader?.AcquireLatestFrame())
        {
            if (faceFrame == null || !faceFrame.IsTrackingIdValid) return;
            var result = faceFrame.FaceFrameResult;
            if (result == null) return;

            var happy      = result.FaceProperties[FaceProperty.Happy];
            var lookAway   = result.FaceProperties[FaceProperty.LookingAway];
            var mouthMoved = result.FaceProperties[FaceProperty.MouthMoved];
            var mouthOpen  = result.FaceProperties[FaceProperty.MouthOpen];
            var leftEye    = result.FaceProperties[FaceProperty.LeftEyeClosed];
            var rightEye   = result.FaceProperties[FaceProperty.RightEyeClosed];

            LastHappy       = ToFloat(happy);
            LastLookingAway = ToFloat(lookAway);
            LastMouthMoved  = ToFloat(mouthMoved);

            // ── 反應時間偵測（Unity 端計時，後端不做）──────────────
            float audioRms = audioSender != null ? audioSender.CurrentAudioRms : 0f;
            int responseMs = -1;
            if (!responseTimeSent && questionAskedTime >= 0f &&
                (mouthMoved == DetectionResult.Yes || mouthOpen == DetectionResult.Yes ||
                 audioRms > audioSpeechThreshold))
            {
                responseMs       = Mathf.RoundToInt((Time.realtimeSinceStartup - questionAskedTime) * 1000f);
                responseTimeSent = true;
            }

            // ── 骨架量測（純算術，不做分類）────────────────────────
            float headDrop      = userId != 0 ? MeasureHeadDrop(userId)      : -999f;
            float leanForward   = userId != 0 ? MeasureLeanForward(userId)   : -999f;
            float shoulderRaise = userId != 0 ? MeasureShoulderRaise(userId) : -999f;
            float spinebaseZ    = userId != 0 ? MeasureSpinebaseZ(userId)    : -999f;
            float sway          = MeasureBodySway();

            // ── 組合 payload 送後端分類 ─────────────────────────────
            string sid = gameController != null
                ? gameController.sessionId
                : PlayerPrefs.GetString("session_id", "unknown");
            var payload = new SensorPayload
            {
                session_id             = sid,
                face_happy             = happy.ToString(),
                face_looking_away      = lookAway.ToString(),
                face_mouth_moved       = mouthMoved.ToString(),
                face_mouth_open        = mouthOpen.ToString(),
                face_left_eye_closed   = leftEye.ToString(),
                face_right_eye_closed  = rightEye.ToString(),
                skel_head_drop         = headDrop,
                skel_lean_forward      = leanForward,
                skel_shoulder_raise    = shoulderRaise,
                skel_spinebase_z       = spinebaseZ,
                body_sway              = sway,
                audio_rms              = audioRms,
                audio_pitch_variance   = audioSender != null ? audioSender.CurrentPitchVariance : 0f,
                skel_handtip_velocity  = _latestHandTipVelocity,
                response_time_ms       = responseMs,
                timestamp              = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0,
            };

            StartCoroutine(PostSensor(payload));
        }
    }

    // ════════════ 骨架量測（只做算術）════════════════════════════════

    /// <summary>head.y − spineShoulder.y（公尺）。負值或 -999 = 未追蹤。</summary>
    float MeasureHeadDrop(long userId)
    {
        var head  = GetJoint(userId, KinectInterop.JointType.Head);
        var spine = GetJoint(userId, KinectInterop.JointType.SpineShoulder);
        return (head.HasValue && spine.HasValue) ? head.Value.y - spine.Value.y : -999f;
    }

    /// <summary>spineBase.z − spineMid.z（公尺）。正值 = 前傾。-999 = 未追蹤。</summary>
    float MeasureLeanForward(long userId)
    {
        var mid  = GetJoint(userId, KinectInterop.JointType.SpineMid);
        var base_ = GetJoint(userId, KinectInterop.JointType.SpineBase);
        return (mid.HasValue && base_.HasValue) ? base_.Value.z - mid.Value.z : -999f;
    }

    /// <summary>avgShoulder.y − spineShoulder.y（公尺）。正值 = 聳肩。-999 = 未追蹤。</summary>
    float MeasureShoulderRaise(long userId)
    {
        var sL    = GetJoint(userId, KinectInterop.JointType.ShoulderLeft);
        var sR    = GetJoint(userId, KinectInterop.JointType.ShoulderRight);
        var spine = GetJoint(userId, KinectInterop.JointType.SpineShoulder);
        if (!sL.HasValue || !sR.HasValue || !spine.HasValue) return -999f;
        return (sL.Value.y + sR.Value.y) / 2f - spine.Value.y;
    }

    /// <summary>SpineBase Z 深度（公尺，距 Kinect 的距離）。-999 = 未追蹤。</summary>
    float MeasureSpinebaseZ(long userId)
    {
        var spineBase = GetJoint(userId, KinectInterop.JointType.SpineBase);
        return spineBase.HasValue ? spineBase.Value.z : -999f;
    }

    /// <summary>SpineBase 位置標準差（公尺）。數值越大 = 身體晃動越多。</summary>
    float MeasureBodySway()
    {
        if (swayHistory.Count < 4) return 0f;
        Vector3 mean = Vector3.zero;
        foreach (var p in swayHistory) mean += p;
        mean /= swayHistory.Count;
        float variance = 0f;
        foreach (var p in swayHistory) variance += (p - mean).sqrMagnitude;
        return Mathf.Sqrt(variance / swayHistory.Count);
    }

    Vector3? GetJoint(long userId, KinectInterop.JointType jt)
    {
        int idx = (int)jt;
        if (kinectManager == null || !kinectManager.IsJointTracked(userId, idx)) return null;
        return kinectManager.GetJointPosition(userId, idx);
    }

    float ToFloat(DetectionResult r) =>
        r == DetectionResult.Yes ? 1f : r == DetectionResult.Maybe ? 0.5f : 0f;

    // ════════════ HTTP POST ═══════════════════════════════════════════

    IEnumerator PostSensor(SensorPayload payload)
    {
        byte[] body = Encoding.UTF8.GetBytes(JsonUtility.ToJson(payload));
        using var req = new UnityWebRequest($"{backendUrl}/sensor/emotion", "POST");
        req.uploadHandler   = new UploadHandlerRaw(body);
        req.downloadHandler = new DownloadHandlerBuffer();
        req.SetRequestHeader("Content-Type", "application/json");
        AuthService.AttachAuthHeader(req);
        yield return req.SendWebRequest();
        if (req.result != UnityWebRequest.Result.Success)
            Debug.LogWarning($"[Emotion] POST 失敗: {req.error}");
    }

    void OnDestroy()
    {
        faceReader?.Dispose();
        faceSource?.Dispose(true);
        bodyReader?.Dispose();
        sensor?.Close();
    }
}

[Serializable]
class SensorPayload
{
    public string session_id;
    // 臉部（Kinect DetectionResult 字串：Yes / No / Maybe / Unknown）
    public string face_happy;
    public string face_looking_away;
    public string face_mouth_moved;
    public string face_mouth_open;
    public string face_left_eye_closed;
    public string face_right_eye_closed;
    // 骨架量測（公尺；-999 = 關節未追蹤）
    public float skel_head_drop;
    public float skel_lean_forward;
    public float skel_shoulder_raise;
    public float skel_spinebase_z;      // SpineBase Z 深度（B 階段）
    // 彙整訊號
    public float  body_sway;
    public float  audio_rms;
    // B 階段擴充欄位
    public float  audio_pitch_variance;
    public float  skel_handtip_velocity;
    public int    response_time_ms;
    public double timestamp;
}
