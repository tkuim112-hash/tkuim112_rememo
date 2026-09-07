using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Networking;
using Windows.Kinect;

/// <summary>
/// 感測資料蒐集器：讀取骨架量測值、麥克風音量，並定期擷取一張 Kinect 彩色
/// 畫面，組成原始 payload 送給後端。情緒分類邏輯完全在後端執行。
///
/// 臉部訊號改由後端呼叫 face-service（py-feat）分析這裡送出的畫面，取代
/// Kinect 內建 Face API（只有 Happy/LookingAway 等 8 個粗糙布林屬性，判斷不出
/// 更細的表情）——詳見專案記憶 project_openface_kinect_emotion_redesign。
/// 這裡因此不再讀 Microsoft.Kinect.Face 的 FaceFrameSource/FaceFrameReader，
/// 也不再需要 BodyFrameReader（原本只是用來取得 Face Tracking ID）。
///
/// 反應時間（OnQuestionAsked → OnMicPressed）對齊長者按下麥克風那一刻，不是
/// 音量超過門檻的那一刻——2026-09-07 稽核（治療師實測回報反應時間數字對不
/// 上）：改用音量偵測時，長者清喉嚨、椅子聲、環境雜音只要夠大聲就會在長者
/// 真正按下麥克風之前把計時器停掉，量出來的時間跟「聽完問題、決定要回答、
/// 按下麥克風」這個真正有意義的反應時間脫鉤。詳見 OnMicPressed 的說明。
/// </summary>
public class KinectSensorSender : MonoBehaviour
{
    [Header("後端設定")]
    public string backendUrl   = "https://api.re-memo.com";
    public float  sendInterval = 2f;

    [Header("外部參考")]
    public GameController    gameController;
    public KinectAudioSender audioSender;

    [Header("臉部畫面設定")]
    [Tooltip("送給後端做臉部分析的 JPEG 品質（0-100）。畫面每 sendInterval 秒送一次，不需要很高品質")]
    [Range(1, 100)]
    public int facialFrameJpegQuality = 60;

    private KinectSensor sensor;

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
    // 是否已進入「長者該回答」的等待期（narration播完/估算閱讀時間到才算開始）。
    // 見 OnQuestionAsked／OnNewQuestionDisplayed 說明——新問題文字剛顯示、
    // 語音還在播放/估算閱讀時間還沒過的這段期間，長者本來就不該開口，這裡
    // 保持 false，讓後端不要把這段合理的沉默/嘴巴沒動當成「投入度低」的證據
    // （2026-09-08 稽核：長者聽題目時本來就不會開口，投入度卻被拉低）。
    private bool  _awaitingResponse = false;
    // OnMicPressed() 當下直接算好存這裡，CollectAndSend() 只負責讀走、送出、
    // 歸零——實際按鈕觸發跟送出頻率脫鉤（後者每 sendInterval 才跑一次），
    // 不能反過來在 CollectAndSend 裡才用「現在」去算時間差，那樣算出來的
    // 會是「按下麥克風到下一次送出感測資料」的間隔，不是真正的反應時間。
    private int _pendingResponseMs = -1;

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
        if (!sensor.IsOpen) sensor.Open();

        // 送去做臉部分析的畫面需要 KinectManager 開啟彩色影像運算，不然
        // GetUsersClrTex2D() 會拿到 null/空白貼圖。這是場景設定（KinectManager
        // Inspector 的 Compute Color Map 勾選），不是這裡的程式碼能保證的，
        // 只能在這裡提醒。
        if (kinectManager != null && !kinectManager.computeColorMap)
            Debug.LogWarning("[Emotion] KinectManager 的 Compute Color Map 未開啟，臉部分析畫面將無法擷取");
    }

    /// <summary>GameController 在 TTS 播完、顯示問題後呼叫，啟動反應時間計時。</summary>
    public void OnQuestionAsked()
    {
        questionAskedTime = Time.realtimeSinceStartup;
        responseTimeSent  = false;
        _pendingResponseMs = -1;
        _awaitingResponse  = true;
    }

    /// <summary>
    /// GameController/ShareController 在新一題的文字剛顯示、準備開始播語音/
    /// 起算估讀秒數之前呼叫，跟 OnQuestionAsked() 成對——這裡標記「narration
    /// 開始，長者還不該開口」，OnQuestionAsked() 觸發時（語音播完或估讀時間
    /// 到）才標記「進入回答等待期」。兩者中間送出的感測幀，後端會知道這段
    /// 沉默/嘴巴沒動是正常的，不當成投入度低的證據。
    /// </summary>
    public void OnNewQuestionDisplayed()
    {
        _awaitingResponse = false;
    }

    /// <summary>
    /// GameController/ShareController 在長者按下麥克風、真正開始錄音那一刻呼叫
    /// （StartRecording() 呼叫 kinectAudioSender.StartSTT() 的同時）。
    ///
    /// 2026-09-07 稽核（治療師實測回報反應時間數字對不上）：原本改成量「音量
    /// 超過門檻的那一刻」，結果長者清喉嚨、椅子聲、環境雜音，只要音量夠大
    /// 就會在長者真正按下麥克風之前把計時器停掉，量出來的時間比「聽完問題、
    /// 決定要回答、按下麥克風」這段真正有意義的反應時間短很多，跟按鈕動作
    /// 完全脫鉤。改回對齊「按下麥克風」這個長者主動的操作，量測的才是使用者
    /// 真正想回答的反應時間，不會被環境噪音誤觸發。
    /// </summary>
    public void OnMicPressed()
    {
        if (!responseTimeSent && questionAskedTime >= 0f)
        {
            _pendingResponseMs = Mathf.RoundToInt((Time.realtimeSinceStartup - questionAskedTime) * 1000f);
            responseTimeSent   = true;
        }
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
        if (!kinectManager.IsJointTracked(userId, (int)KinectInterop.JointType.SpineBase))
        {
            // SpineBase 沒追蹤到這幀就直接跳過，不會呼叫下面的 SampleHandTipVelocity()。
            // 若中間跳過了不只一個取樣間隔，追蹤恢復後 _prevHandTipL/R 就是好幾個間隔前的
            // 舊位置，但 SampleHandTipVelocity 仍會照樣除以固定的 SWAY_SAMPLE_INTERVAL，
            // 把跨越較長時間的位移硬算成虛高速度。這裡讓追蹤中斷時一併重置
            // _prevHandTipValid，恢復追蹤後只重新記錄基準位置、不計算這一次的速度，
            // 跟 HandTip 關節本身沒追蹤到時的既有處理方式一致。
            _prevHandTipValid = false;
            return;
        }
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
        // ── 反應時間讀取（Unity 端計時，後端不做）──────────────────
        // 真正的計時/停表在 OnMicPressed()（長者按下麥克風那一刻），這裡只是
        // 把算好的值讀走、送給後端，送完歸零，避免同一個值被下一次
        // CollectAndSend 重複送出。
        float audioRms = audioSender != null ? audioSender.CurrentAudioRms : 0f;
        int responseMs = _pendingResponseMs;
        _pendingResponseMs = -1;

        // ── 骨架量測（純算術，不做分類）────────────────────────────
        float headDrop        = userId != 0 ? MeasureHeadDrop(userId)             : -999f;
        float leanForward     = userId != 0 ? MeasureLeanForward(userId)          : -999f;
        float shoulderRaise   = userId != 0 ? MeasureShoulderRaise(userId)        : -999f;
        float spinebaseZ      = userId != 0 ? MeasureSpinebaseZ(userId)           : -999f;
        float elbowFlareLeft  = userId != 0 ? MeasureElbowFlare(userId, true)     : -999f;
        float elbowFlareRight = userId != 0 ? MeasureElbowFlare(userId, false)    : -999f;
        float sway            = MeasureBodySway();

        // ── 組合 payload 送後端分類 ─────────────────────────────────
        string sid = gameController != null
            ? gameController.sessionId
            : (AuthSession.SessionId ?? "unknown");
        var payload = new SensorPayload
        {
            session_id             = sid,
            skel_head_drop         = headDrop,
            skel_lean_forward      = leanForward,
            skel_shoulder_raise    = shoulderRaise,
            skel_spinebase_z       = spinebaseZ,
            skel_elbow_flare_left  = elbowFlareLeft,
            skel_elbow_flare_right = elbowFlareRight,
            body_sway              = sway,
            audio_rms              = audioRms,
            audio_pitch_variance   = audioSender != null ? audioSender.CurrentPitchVariance : 0f,
            skel_handtip_velocity  = _latestHandTipVelocity,
            response_time_ms       = responseMs,
            timestamp              = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0,
            awaiting_response      = _awaitingResponse,
        };

        byte[] jpegFrame = CaptureFacialFrameJpeg();
        StartCoroutine(PostSensor(payload, jpegFrame));
    }

    /// <summary>
    /// 擷取 Kinect 彩色畫面並編碼成 JPEG，送給後端做臉部分析（face-service）。
    /// 需要 KinectManager.computeColorMap 開啟；沒有可用畫面時回傳 null，
    /// 呼叫端要能正常處理「這次沒有臉部畫面」，不擋住骨架/語音訊號照常送出。
    /// </summary>
    byte[] CaptureFacialFrameJpeg()
    {
        if (kinectManager == null) return null;
        Texture2D colorTex = kinectManager.GetUsersClrTex2D();
        if (colorTex == null || colorTex.width == 0 || colorTex.height == 0) return null;

        try
        {
            return colorTex.EncodeToJPG(facialFrameJpegQuality);
        }
        catch (Exception e)
        {
            Debug.LogWarning($"[Emotion] 臉部畫面編碼失敗: {e.Message}");
            return null;
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

    /// <summary>
    /// |elbow.x − spineMid.x|（公尺）。左右手肘各自算，不在這裡合併成一個值——
    /// 後端要用 min(左, 右) 判斷身體收縮姿勢，因為長者操作介面（見
    /// HandCursorRemapper）只用單手舉手控制游標，操作中的那隻手肘距離只會
    /// 變大，另一隻沒在操作的手肘才是判斷收縮姿勢時真正可信的一側，見
    /// app/routers/sensor.py _is_body_constricted 的完整說明。-999 = 未追蹤。
    /// </summary>
    float MeasureElbowFlare(long userId, bool leftSide)
    {
        var elbow = GetJoint(userId, leftSide ? KinectInterop.JointType.ElbowLeft : KinectInterop.JointType.ElbowRight);
        var mid   = GetJoint(userId, KinectInterop.JointType.SpineMid);
        if (!elbow.HasValue || !mid.HasValue) return -999f;
        return Mathf.Abs(elbow.Value.x - mid.Value.x);
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

    // ════════════ HTTP POST ═══════════════════════════════════════════

    /// <summary>
    /// 改成 multipart/form-data：payload 欄位是原本的 JSON（骨架/語音訊號不變），
    /// frame 欄位是可選的 JPEG 畫面（沒抓到畫面時就不帶這個欄位）。後端
    /// （/sensor/emotion）收到 frame 才會呼叫 face-service 做臉部分析，沒有
    /// frame 時臉部訊號視為中性值，不影響骨架/語音訊號正常送出跟累積。
    /// </summary>
    IEnumerator PostSensor(SensorPayload payload, byte[] jpegFrame)
    {
        var form = new List<IMultipartFormSection>
        {
            new MultipartFormDataSection("payload", JsonUtility.ToJson(payload)),
        };
        if (jpegFrame != null)
            form.Add(new MultipartFormFileSection("frame", jpegFrame, "frame.jpg", "image/jpeg"));

        using var req = UnityWebRequest.Post($"{backendUrl}/sensor/emotion", form);
        AuthService.AttachAuthHeader(req);
        yield return req.SendWebRequest();
        if (req.result != UnityWebRequest.Result.Success)
            Debug.LogWarning($"[Emotion] POST 失敗: {req.error}");
    }

    void OnDestroy()
    {
        sensor?.Close();
    }
}

[Serializable]
class SensorPayload
{
    public string session_id;
    // 臉部訊號改由 Unity 另外送一張 JPEG 畫面（見 PostSensor 的 multipart
    // frame 欄位），後端呼叫 face-service 分析，這裡不再放 Kinect Face API
    // 的布林欄位。
    // 骨架量測（公尺；-999 = 關節未追蹤）
    public float skel_head_drop;
    public float skel_lean_forward;
    public float skel_shoulder_raise;
    public float skel_spinebase_z;      // SpineBase Z 深度（B 階段）
    public float skel_elbow_flare_left;   // |elbowLeft.x − spineMid.x|（C 階段，身體收縮姿勢）
    public float skel_elbow_flare_right;  // |elbowRight.x − spineMid.x|（C 階段，身體收縮姿勢）
    // 彙整訊號
    public float  body_sway;
    public float  audio_rms;
    // B 階段擴充欄位
    public float  audio_pitch_variance;
    public float  skel_handtip_velocity;
    public int    response_time_ms;
    public double timestamp;
    // true = 已進入長者該回答的等待期（narration播完/估讀時間到）；
    // false = 新問題剛顯示、還在播語音或估讀時間內，長者本來就不該開口，
    // 見 KinectSensorSender.OnNewQuestionDisplayed／OnQuestionAsked 說明。
    public bool   awaiting_response;
}
