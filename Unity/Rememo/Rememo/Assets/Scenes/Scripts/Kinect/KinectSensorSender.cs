using System;
using System.Collections;
using System.Collections.Generic;
using System.Text;
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
    // 新問題文字一顯示（OnNewQuestionDisplayed）就設，一定比 questionAskedTime
    // （narration播完/估讀時間到才設）早。OnMicPressed() 找不到 questionAskedTime
    // 時退回用這個當基準，見該方法 2026-09-08 第二次稽核的說明。
    private float _questionDisplayedTime = -1f;
    private bool  responseTimeSent  = false;
    // 是否已進入「長者該回答」的等待期（narration播完/估算閱讀時間到才算開始）。
    // 見 OnQuestionAsked／OnNewQuestionDisplayed 說明——新問題文字剛顯示、
    // 語音還在播放/估算閱讀時間還沒過的這段期間，長者本來就不該開口，這裡
    // 保持 false，讓後端不要把這段合理的沉默/嘴巴沒動當成「投入度低」的證據
    // （2026-09-08 稽核：長者聽題目時本來就不會開口，投入度卻被拉低）。
    private bool  _awaitingResponse = false;
    // 2026-09-08前：OnMicPressed() 算好存個 _pendingResponseMs，等
    // CollectAndSend() 每 sendInterval（2秒）跑一次才讀走送出。改成
    // OnMicPressed() 當下直接呼叫 PostResponseTime() 送給後端（見該方法
    // 說明），不再需要這個緩衝欄位——回合可能在長者按下麥克風後很快就
    // 結束，等下一次 CollectAndSend 常常來不及趕在回合結算前送到後端，
    // 導致那個回合的反應時間永遠讀到 0 筆樣本、被治療師端誤顯示成「0秒」。

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

    /// <summary>
    /// GameController 在 TTS 播完、顯示問題後呼叫，把反應時間的計時基準從
    /// OnNewQuestionDisplayed() 設的「文字剛顯示」推進到「narration/估讀
    /// 時間也結束」這個更精確的起點。responseTimeSent 不在這裡重置——已經
    /// 交給 OnNewQuestionDisplayed() 在新問題一出現時處理一次就好，這裡如果
    /// 又重置一次，長者在這之前就已經按過麥克風送出過一次反應時間的話，
    /// 之後同一題再按（重新錄音）會被誤判成「還沒送過」，變成同一題送兩次。
    /// </summary>
    public void OnQuestionAsked()
    {
        questionAskedTime  = Time.realtimeSinceStartup;
        _awaitingResponse  = true;
    }

    /// <summary>
    /// GameController/ShareController 在新一題的文字剛顯示、準備開始播語音/
    /// 起算估讀秒數之前呼叫，跟 OnQuestionAsked() 成對——這裡標記「narration
    /// 開始，長者還不該開口」，OnQuestionAsked() 觸發時（語音播完或估讀時間
    /// 到）才標記「進入回答等待期」。兩者中間送出的感測幀，後端會知道這段
    /// 沉默/嘴巴沒動是正常的，不當成投入度低的證據。
    ///
    /// 2026-09-08 第二次稽核（第三回合單輪問答那種只有一題的回合，反應
    /// 時間整場永遠是空的）：這裡也要把 responseTimeSent 重置、questionAskedTime
    /// 清掉、記錄 _questionDisplayedTime——麥克風按鈕從文字一顯示就能按，
    /// 不會等 narration/估讀時間跑完才解鎖，如果長者（或治療師操作跳過
    /// 等待）在 OnQuestionAsked() 真正觸發之前就按下麥克風，原本
    /// responseTimeSent 還停留在「上一題已經送過」的 true，OnMicPressed()
    /// 的判斷式會直接跳過，這一題就永遠量不到反應時間；只有一題的回合
    /// 沒有下一題可以補救，就會整回合空白。這裡提前重置，讓 OnMicPressed()
    /// 隨時都能量到東西（正式觸發前用 _questionDisplayedTime 當退回基準）。
    /// </summary>
    public void OnNewQuestionDisplayed()
    {
        _questionDisplayedTime = Time.realtimeSinceStartup;
        questionAskedTime      = -1f;
        responseTimeSent       = false;
        _awaitingResponse      = false;
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
    ///
    /// 2026-09-08 稽核（治療師反映某回合反應時間顯示「0秒」，長者其實有
    /// 正常回答）：這裡算出反應時間後，改成當下直接呼叫 PostResponseTime()
    /// 送給後端，不再等 CollectAndSend() 的 2 秒定時器——STT辨識＋治療師
    /// 審核確認＋長者按送出，這整段路徑通常都比 2 秒長，但沒辦法保證一定
    /// 夠長；回合一旦在這之前結束，後端 _finalize_round_response_time 會
    /// 在算平均時讀到 0 筆樣本，直接跳過不寫，這個回合的反應時間就永遠是
    /// 空的，被治療師端誤顯示成「0秒」。
    ///
    /// 2026-09-08 第二次稽核：questionAskedTime 要等 narration/估讀時間跑完
    /// 才會被 OnQuestionAsked() 設成有效值，但麥克風按鈕沒有跟著鎖住，長者
    /// 答得夠快（或操作端提前按）時，這裡執行當下 questionAskedTime 可能
    /// 還沒被設定。以前的寫法遇到這狀況會整個跳過、什麼都不送，這裡改成
    /// 退回用 _questionDisplayedTime（文字剛顯示那一刻，一定有效）當基準——
    /// 量出來的數字會把 narration/估讀時間也算進去、比嚴格定義的「反應時間」
    /// 略長，但總比整回合永遠空白、被治療師端誤讀成「秒答」好。
    /// </summary>
    public void OnMicPressed()
    {
        if (responseTimeSent) return;
        float anchor = questionAskedTime >= 0f ? questionAskedTime : _questionDisplayedTime;
        if (anchor < 0f) return;

        int ms = Mathf.RoundToInt((Time.realtimeSinceStartup - anchor) * 1000f);
        responseTimeSent = true;
        StartCoroutine(PostResponseTime(ms));
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
        // ── 反應時間（Unity 端計時，後端不做）──────────────────────
        // 計時/停表在 OnMicPressed()，算好當下就直接呼叫 PostResponseTime()
        // 送出（見該方法說明），不再靠這支週期性 payload 夾帶，所以這裡
        // response_time_ms 固定填 -1，後端收到會直接略過。
        float audioRms = audioSender != null ? audioSender.CurrentAudioRms : 0f;

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
            response_time_ms       = -1,
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

    /// <summary>
    /// OnMicPressed() 當下呼叫，把剛算好的反應時間立即送給後端，不跟主要的
    /// 骨架/語音 payload 混在一起送、也不等 CollectAndSend 的 2 秒定時器——
    /// 見 OnMicPressed 說明，這裡要搶在回合結算前送到。
    /// </summary>
    IEnumerator PostResponseTime(int responseMs)
    {
        string sid = gameController != null
            ? gameController.sessionId
            : (AuthSession.SessionId ?? "unknown");
        string json = JsonUtility.ToJson(new ResponseTimePayload
        {
            session_id       = sid,
            response_time_ms = responseMs,
        });

        using var req = new UnityWebRequest($"{backendUrl}/sensor/response_time", "POST");
        req.uploadHandler   = new UploadHandlerRaw(Encoding.UTF8.GetBytes(json));
        req.downloadHandler = new DownloadHandlerBuffer();
        req.SetRequestHeader("Content-Type", "application/json");
        AuthService.AttachAuthHeader(req);
        yield return req.SendWebRequest();
        if (req.result != UnityWebRequest.Result.Success)
            Debug.LogWarning($"[Emotion] 反應時間即時回報失敗: {req.error}");
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

/// <summary>OnMicPressed() 立即回報反應時間用，見 PostResponseTime 說明。</summary>
[Serializable]
class ResponseTimePayload
{
    public string session_id;
    public int    response_time_ms;
}
