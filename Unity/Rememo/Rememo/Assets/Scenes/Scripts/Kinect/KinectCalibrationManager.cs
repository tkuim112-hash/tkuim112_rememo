using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Networking;
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
    private List<float> lookingAwayBuffer = new List<float>();
    private List<float> mouthMovedBuffer = new List<float>();
    // 表情正負向判斷用到的 AU（AU06/AU12 微笑、AU01/04/05/07/15/23 皺眉），
    // 校正期間逐一 AU 收集個人靜止時的強度基準，供後端 sensor.py _au_baseline/
    // _au_c 逐一扣除——不是像舊版 happyBaseline/frownBaseline 那樣把好幾個 AU
    // 混成一個籠統的數字，長者臉部因皮膚鬆弛、法令紋只會讓特定 AU（例如
    // AU04）天生偏高，逐一對應才不會連帶稀釋其他 AU 的訊號。key 是 AU 代碼
    // （跟後端 face_calibration_sample 回傳的 au_codes 一致），value 是這場
    // 校正期間收到的所有樣本，最後在 SendCalibrationData 取平均。
    private Dictionary<string, List<float>> auIntensityBuffers = new Dictionary<string, List<float>>();
    private List<float> _pitchVarBuffer  = new List<float>();
    private List<float> _rmsBuffer       = new List<float>();

    // 臉部訊號改由後端 face-service 分析（見 KinectSensorSender 類別開頭註解），
    // 校正期間不再逐幀讀本地 Kinect Face API 值。這裡是在算 15 秒內的平均值，
    // 樣本數直接決定基準值準不準，所以不用固定間隔的保守取樣（會白白浪費
    // face-service 處理完的時間），改成「上一次請求做完立刻打下一次」的
    // 自我節奏連續取樣（EmotionSamplingLoop），取樣速度自然貼齊 face-service
    // 真正的往返時間，在 15 秒視窗內盡量多收樣本。
    private Coroutine _emotionSamplingLoop = null;

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

    // SendAsync 只代表送出動作沒出錯，不代表後端真的收到、存進 Redis；
    // 這兩個旗標由 OnMessage（背景執行緒）寫入，只能用 volatile bool 這種簡單旗標跨執行緒讀取，
    // 不能在 OnMessage 裡直接動 UI 或設 IsCalibrated（Unity API 不是執行緒安全的）。
    private volatile bool ackReceived = false;
    private volatile bool ackSuccess = false;
    private string sessionId;

    // ws.ConnectAsync() 的 TLS handshake 到 wss://api.re-memo.com 實測可能拖到
    // 30～40 秒以上（跟 KinectAudioSender 的 /ws/stt 是同一個網域、同一套
    // WebSocketSharp，2026-08-21 那邊已經踩過同樣的坑）。15 秒的基準值蒐集常常
    // 比連線本身還快跑完，若這時 ws 還沒 Open，不能整批放棄重新蒐集——資料先
    // 存成 pending，等 OnOpen 真正觸發（背景執行緒）時再補送，跟 KinectAudioSender
    // 的 _pendingStart/_pendingEnd 是同一套模式。
    private volatile bool _pendingCalibrationSend = false;
    private volatile string _pendingCalibrationJson = null;
    // WaitForServerAck 要知道資料「真的送出去了没」，才能決定 5 秒 ack 逾時要從
    // 什麼時候開始算——不能從呼叫 SendCalibrationData() 當下就開始算，那時候
    // 資料很可能還在等連線開，5 秒還沒到 TLS handshake 都還沒做完。
    private volatile bool _calibrationDataSent = false;

    void Start()
    {
        kinectManager = KinectManager.Instance;
        sensorSender = GetComponent<KinectSensorSender>();

        if (audioSender == null)
            Debug.LogWarning("[Calibration] audioSender 未設定，pitchVarianceBaseline 將為 0（個人化音高門檻無效）");

        SetStatus(false);

        // session_id 必須已經在 AuthSession 中（由前一個 Scene 建立），才能讓後端把校正基準與本次療程綁定
        sessionId = AuthSession.SessionId ?? "";
        if (string.IsNullOrEmpty(sessionId))
            Debug.LogWarning("[Calibration] AuthSession 中無 session_id，校正基準將無法存入 Redis");
        string wsUrl = string.IsNullOrEmpty(sessionId)
            ? calibrationUrl
            : $"{calibrationUrl}?session_id={sessionId}";
        wsUrl = AuthService.AppendToken(wsUrl);

        ws = new WebSocket(wsUrl);
        ws.SslConfiguration.EnabledSslProtocols = System.Security.Authentication.SslProtocols.Tls12;
        ws.OnOpen += (s, e) =>
        {
            Debug.Log("[Calibration WS] 已連線");
            // 蒐集完畢時 ws 若還沒 Open，SendCalibrationData 會把資料存到這裡；
            // 現在連線真的開了，補送出去（同樣邏輯見 KinectAudioSender.ConnectWebSocket）。
            if (_pendingCalibrationSend && _pendingCalibrationJson != null)
            {
                string json = _pendingCalibrationJson;
                _pendingCalibrationSend = false;
                ws.SendAsync(json, sent =>
                {
                    if (!sent)
                        Debug.LogWarning("[Calibration] 補送 SendAsync 回傳失敗，本地端送出動作本身就沒成功");
                });
                _calibrationDataSent = true;
            }
        };
        ws.OnError += (s, e) => Debug.LogError($"[Calibration WS] 錯誤: {e.Message}");
        // 後端存成功/失敗都會回一個 JSON（見 ws_calibration.py）；這裡才是真正的送達確認，
        // CalibrationRoutine 的 WaitForServerAck 會 poll 這兩個旗標決定要不要標記校正完成。
        ws.OnMessage += (s, e) =>
        {
            Debug.Log($"[Calibration WS] 後端回應: {e.Data}");
            try
            {
                ackSuccess = JsonUtility.FromJson<CalibrationAck>(e.Data).ok;
            }
            catch
            {
                ackSuccess = false;
            }
            ackReceived = true;
        };
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
        _emotionSamplingLoop = StartCoroutine(EmotionSamplingLoop());

        while (calibrationTimer < calibrationDuration)
        {
            calibrationTimer += Time.deltaTime;

            if (progressBar != null)
                progressBar.value = calibrationTimer / calibrationDuration;

            CollectSkeletonData();
            CollectPitchData();
            CollectAudioData();

            yield return null;
        }

        if (_emotionSamplingLoop != null)
        {
            StopCoroutine(_emotionSamplingLoop);
            _emotionSamplingLoop = null;
        }

        // CheckStability 只看 SpineBase 這一點晃不晃，就算只有半個人在鏡頭裡、
        // 其他關節整場都沒被追蹤到，只要 SpineBase 穩定就會判定通過。
        // CheckJointCompleteness 補上「人有沒有完整在鏡頭範圍內」的檢查。
        bool isStable = CheckStability() && CheckJointCompleteness();

        if (isStable)
        {
            ApplyCursorRemapping(); // ── 新增
            SendCalibrationData();

            if (string.IsNullOrEmpty(sessionId))
            {
                // 離線/demo 模式沒有 session_id，後端本來就會拒存（見 ws_calibration.py），
                // 沒有真正的療程可供治療師端確認，等後端 ack 沒有意義，沿用舊行為直接放行。
                IsCalibrated = true;
                SetStatus(true);
                Debug.Log("[Calibration] 無 session_id（離線/demo），略過後端確認直接標記校正完成");
            }
            else
            {
                Debug.Log("[Calibration] 基準值已送出，等待後端確認…");
                // IsCalibrated 不在這裡就設 true：成功圖示/場景切換都掛在 IsCalibrated 上，
                // 若送出當下就標記完成，WarmupController 可能在資料真的送達前就把
                // 這個物件連同 WebSocket 一起銷毀（OnDestroy → ws.Close()），資料就送不到後端，
                // 而後端那邊會靜默吞掉這個斷線（見 ws_calibration.py 的 WebSocketDisconnect），
                // 完全不留 log。所以要等 WaitForServerAck 真的收到後端 {"ok": true} 才算數。
                yield return StartCoroutine(WaitForServerAck());
            }
        }
        else
        {
            RetryCalibration("數據不穩定或關節追蹤不完整，重新校正");
        }
    }

    IEnumerator WaitForServerAck()
    {
        // ws 的 TLS handshake 實測可能拖到 30～40 秒以上（見類別開頭 _pendingCalibrationSend
        // 的說明），所以先給連線一段夠寬鬆的時間把資料真正送出去；送出後，後端存 Redis
        // 本身很快，5 秒 ack 逾時才從這裡開始算才有意義。
        const float sendWaitTimeoutSeconds = 60f;
        const float ackTimeoutSeconds = 5f;

        ackReceived = false;
        ackSuccess = false;

        float waitedForSend = 0f;
        while (!_calibrationDataSent && waitedForSend < sendWaitTimeoutSeconds)
        {
            waitedForSend += Time.deltaTime;
            yield return null;
        }

        if (!_calibrationDataSent)
        {
            RetryCalibration($"等待 WebSocket 連線超過 {sendWaitTimeoutSeconds:F0} 秒仍未送出基準值，重新校正");
            yield break;
        }

        float waited = 0f;
        while (!ackReceived && waited < ackTimeoutSeconds)
        {
            waited += Time.deltaTime;
            yield return null;
        }

        if (ackReceived && ackSuccess)
        {
            IsCalibrated = true;
            SetStatus(true);
            Debug.Log("[Calibration] 後端已確認收到基準值，校正完成（見 WarmupController）");
            // 場景切換交給 WarmupController：這裡只負責把 IsCalibrated 設為 true，
            // WarmupController 會接著自己 poll 後端狀態，一偵測到治療師按下「啟動療程」
            // （/session/start 被呼叫、回 requested=true，不等 LLM/RAG/TTS 跑完）就切去
            // InstructionScene，真正耗時的生成過程改到說明頁用進度條呈現。
        }
        else if (ackReceived)
        {
            RetryCalibration("後端回應校正資料儲存失敗（見上方 [Calibration WS] 後端回應），重新校正");
        }
        else
        {
            RetryCalibration($"等待後端確認超過 {ackTimeoutSeconds:F0} 秒仍無回應，重新校正");
        }
    }

    void RetryCalibration(string reason)
    {
        Debug.Log($"[Calibration] {reason}");
        skeletonBuffer.Clear();
        lookingAwayBuffer.Clear();
        mouthMovedBuffer.Clear();
        auIntensityBuffers.Clear();
        if (_emotionSamplingLoop != null)
        {
            StopCoroutine(_emotionSamplingLoop);
            _emotionSamplingLoop = null;
        }
        ClearGeometryBuffers(); // ── 新增
        _pendingCalibrationSend = false;
        _pendingCalibrationJson = null;
        _calibrationDataSent = false;
        StartCoroutine(CalibrationRoutine());
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

    void CollectPitchData()
    {
        // 靜音期間的音高變異值不具代表性，僅在有聲音時蒐集
        if (audioSender != null && audioSender.CurrentAudioRms > 0.005f)
            _pitchVarBuffer.Add(audioSender.CurrentPitchVariance);
    }

    /// <summary>
    /// 音量門檻校正，刻意跟 CollectPitchData 相反——這裡要量的是「校正這 15 秒
    /// 沒特別要求長者開口時」的底噪水準（環境音＋Kinect 陣列麥克風本身的量測
    /// 雜訊），所以每一幀都收，不像音高只在偵測到聲音時才收。校正窗口本來就
    /// 只要求長者坐穩、不要求開口說話，收到的樣本本來就以底噪為主，用「底噪
    /// 之上一點」設語音門檻，才能讓小聲/離 Kinect 較遠的長者說話時也能超過
    /// 門檻，同時不需要改動校正流程去要求長者刻意講話。詳見後端 sensor.py
    /// _audio_threshold 的完整說明（雙方個人化公式必須同步）。
    /// </summary>
    void CollectAudioData()
    {
        if (audioSender != null)
            _rmsBuffer.Add(audioSender.CurrentAudioRms);
    }

    /// <summary>
    /// 自我節奏連續取樣：上一次 CollectEmotionSample() 做完（不管成功或失敗）
    /// 立刻開始下一次，不用固定計時器等待。取樣速度自然貼齊 face-service
    /// 真正的往返時間，15 秒視窗內能收多少樣本就收多少，準確度優先。
    /// 由 CalibrationRoutine 在開始蒐集時啟動、蒐集結束時 StopCoroutine 停止。
    /// </summary>
    IEnumerator EmotionSamplingLoop()
    {
        while (true)
        {
            yield return StartCoroutine(CollectEmotionSample());
        }
    }

    /// <summary>
    /// 拍一張 Kinect 彩色畫面，打後端 /sensor/face_calibration_sample 换算成
    /// 跟舊版 Kinect DetectionResult 同尺度的分數，塞進 happy/lookingAway/
    /// mouthMoved 三個 buffer。face-service 沒偵測到臉/請求失敗時直接跳過
    /// 這次取樣，不影響其餘校正流程（骨架穩定度判斷不依賴這幾個 buffer）。
    /// </summary>
    IEnumerator CollectEmotionSample()
    {
        if (kinectManager == null) yield break;
        Texture2D colorTex = kinectManager.GetUsersClrTex2D();
        if (colorTex == null || colorTex.width == 0 || colorTex.height == 0) yield break;

        byte[] jpeg;
        try
        {
            jpeg = colorTex.EncodeToJPG(60);
        }
        catch (Exception e)
        {
            Debug.LogWarning($"[Calibration] 臉部畫面編碼失敗: {e.Message}");
            yield break;
        }

        var form = new List<IMultipartFormSection>
        {
            new MultipartFormFileSection("frame", jpeg, "frame.jpg", "image/jpeg"),
        };
        string backendUrl = sensorSender != null ? sensorSender.backendUrl : "https://api.re-memo.com";
        using var req = UnityWebRequest.Post($"{backendUrl}/sensor/face_calibration_sample", form);
        AuthService.AttachAuthHeader(req);
        yield return req.SendWebRequest();
        if (req.result != UnityWebRequest.Result.Success)
        {
            Debug.LogWarning($"[Calibration] 臉部校正取樣失敗: {req.error}");
            yield break;
        }

        FaceCalibrationSample sample;
        try
        {
            sample = JsonUtility.FromJson<FaceCalibrationSample>(req.downloadHandler.text);
        }
        catch (Exception e)
        {
            Debug.LogWarning($"[Calibration] 臉部校正取樣回應解析失敗: {e.Message}");
            yield break;
        }

        lookingAwayBuffer.Add(sample.looking_away);
        mouthMovedBuffer.Add(sample.mouth_moved);

        if (sample.au_codes != null && sample.au_values != null
            && sample.au_codes.Length == sample.au_values.Length)
        {
            for (int i = 0; i < sample.au_codes.Length; i++)
            {
                string code = sample.au_codes[i];
                if (!auIntensityBuffers.TryGetValue(code, out var list))
                {
                    list = new List<float>();
                    auIntensityBuffers[code] = list;
                }
                list.Add(sample.au_values[i]);
            }
        }
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
        _rmsBuffer.Clear();
    }
    // ───────────────────────────────────────────────────

    void SendCalibrationData()
    {
        if (ws == null)
        {
            Debug.LogWarning("[Calibration] SendCalibrationData 被呼叫時 ws 尚未建立，資料未送出");
            return;
        }

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

        float pitchVarMean = Average(_pitchVarBuffer);
        float rmsMean = Average(_rmsBuffer);

        // 表情正負向的個人 AU 基準：逐一 AU 取平均，供後端 _au_baseline 比對
        // （見 auIntensityBuffers 宣告處說明），取代舊版 happyBaseline/
        // frownBaseline 那種把好幾個 AU 混成一個籠統數字的做法。
        var auBaselineCodes = new List<string>();
        var auBaselineValues = new List<float>();
        foreach (var kvp in auIntensityBuffers)
        {
            auBaselineCodes.Add(kvp.Key);
            auBaselineValues.Add(Average(kvp.Value));
        }

        var payload = new CalibrationPayload
        {
            type = "calibration",
            duration = calibrationDuration,
            lookingAwayBaseline = Average(lookingAwayBuffer),
            mouthMovedBaseline = Average(mouthMovedBuffer),
            auBaselineCodes = auBaselineCodes.ToArray(),
            auBaselineValues = auBaselineValues.ToArray(),
            pitchVarianceBaseline = pitchVarMean,
            // 後端門檻公式用 baseline + k×標準差（見 app/routers/sensor.py
            // _pitch_threshold），比單純乘固定倍數更能反映每個人音高變異本身的
            // 離散程度——UpdatePitch() 的自相關法沒有正規化，對不同人可能有
            // 系統性偏差，但這個偏差在校正基準跟即時量測上是同一套算法量出來的，
            // 用「相對自己校正期間分布」設門檻，能大致抵消掉這個偏差。
            pitchVarianceStdDev = StdDev(_pitchVarBuffer, pitchVarMean),
            // 校正期間量到的底噪水準，供後端 _audio_threshold 算個人化語音門檻
            // （用於情緒/沉默率判斷，見 CollectAudioData 說明；跟 Unity 本地的
            // 反應時間量測已改用 OnMicPressed 對齊按鍵動作，兩者互不相關）。
            audioRmsBaseline = rmsMean,
            audioRmsStdDev = StdDev(_rmsBuffer, rmsMean),
            jointKeys = new List<string>(baselineJoints.Keys).ToArray(),
            jointX = GetAxis(baselineJoints, 0),
            jointY = GetAxis(baselineJoints, 1),
            jointZ = GetAxis(baselineJoints, 2),
            // 個人靜坐晃動基準，供後端 sensor.py _body_sway_threshold 當
            // 「baseline + 固定邊際」的個人門檻用（見該函式說明：長者本體
            // 感覺/視覺/前庭覺隨年齡退化，靜止時的姿勢晃動幅度本身就普遍
            // 比年輕人大，固定絕對門檻對這個族群風險最高，body_sway 又在
            // agitation 占最大權重）。
            bodySwayBaseline = MeasureBodySwayBaseline()
        };

        string json = JsonUtility.ToJson(payload);

        if (ws.ReadyState == WebSocketState.Open)
        {
            ws.SendAsync(json, sent =>
            {
                if (!sent)
                    Debug.LogWarning("[Calibration] SendAsync 回傳失敗，本地端送出動作本身就沒成功");
            });
            _calibrationDataSent = true;
        }
        else
        {
            // TLS handshake 還沒做完，ws 還不是 Open——資料先存著，OnOpen 觸發時
            // 自動補送（見 Start() 裡 ws.OnOpen 的邏輯），不要整批放棄重新蒐集。
            Debug.Log("[Calibration] WebSocket 尚未連線，基準值先暫存，連線後自動補送");
            _pendingCalibrationJson = json;
            _pendingCalibrationSend = true;
        }
    }

    /// <summary>
    /// 校正期間的 SpineBase 位置標準差（公尺）——跟 KinectSensorSender.cs
    /// MeasureBodySway() 同一套算法（3D 位置變異量的均方根：算出平均位置、
    /// 取每一幀跟平均位置的平方距離、取均方根），只是那邊是療程中即時對
    /// 6 秒滑動視窗算，這裡是對整個 15 秒校正視窗的樣本算一次，量出這個人
    /// 「平靜坐著時原本」的晃動基準。直接沿用 skeletonBuffer 裡已經蒐集到
    /// 的 SpineBase 逐幀位置（本來就有記錄，見 CollectSkeletonData），
    /// 不需要額外的 buffer。樣本數太少（&lt;4，跟 MeasureBodySway 的下限
    /// 一致）時回傳 0，後端 _body_sway_threshold 會視為「沒有有效基準」
    /// 退回固定門檻。
    /// </summary>
    float MeasureBodySwayBaseline()
    {
        var positions = new List<Vector3>();
        foreach (var frame in skeletonBuffer)
        {
            if (frame.TryGetValue("SpineBase", out var pos))
                positions.Add(new Vector3(pos[0], pos[1], pos[2]));
        }
        if (positions.Count < 4) return 0f;

        Vector3 mean = Vector3.zero;
        foreach (var p in positions) mean += p;
        mean /= positions.Count;

        float variance = 0f;
        foreach (var p in positions) variance += (p - mean).sqrMagnitude;
        return Mathf.Sqrt(variance / positions.Count);
    }

    float Average(List<float> list)
    {
        if (list.Count == 0) return 0f;
        float sum = 0f;
        foreach (var v in list) sum += v;
        return sum / list.Count;
    }

    float StdDev(List<float> list, float mean)
    {
        if (list.Count == 0) return 0f;
        float sumSq = 0f;
        foreach (var v in list) sumSq += (v - mean) * (v - mean);
        return Mathf.Sqrt(sumSq / list.Count);
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

    // 2026-08-26稽核（code review 再稽核修正）：場景卸載時如果
    // ws 還在背景執行緒讀取 frame header，直接 Close() 會把讀到一半的
    // stream 硬切斷，WebSocketSharp 內部把這個情況標成 Fatal 等級丟出
    // WebSocketException（"The header of a frame cannot be read from the
    // stream."）。這個時間點資料（基準值）早就送達、後端也已經回 ok，純粹
    // 是關閉時機不乾淨的噪音，不影響功能。
    // 原本只在 ReadyState == Open 才呼叫 Close()，但這個檔案自己上面的
    // 註解就寫了 TLS handshake 可能拖到 30～40 秒、比15秒的校正視窗還長——
    // 如果場景在 ws 還處於 Connecting（尚未變成 Open）時就被卸載，改成只
    // 判斷 Open 會讓 Close() 完全不會被呼叫，連線/背景執行緒永遠不會被
    // 關掉，比原本「不管狀態一律呼叫 Close()」還倒退。改成 Open／
    // Connecting 都呼叫 Close()（覆蓋原本所有非 Closed/Closing 的情況），
    // 外層包 try/catch 吞掉上述已知無害的 Fatal 噪音，兩個問題一起解決。
    void OnDestroy()
    {
        if (ws == null) return;
        if (ws.ReadyState != WebSocketState.Open && ws.ReadyState != WebSocketState.Connecting)
            return;
        try { ws.Close(); }
        catch (Exception e) { Debug.LogWarning($"[Calibration WS] 關閉時發生例外（可忽略）: {e.Message}"); }
    }
}

[System.Serializable]
public class CalibrationPayload
{
    public string type;
    public float duration;
    public float lookingAwayBaseline;
    public float mouthMovedBaseline;
    public string[] auBaselineCodes;
    public float[] auBaselineValues;
    public float pitchVarianceBaseline;
    public float pitchVarianceStdDev;
    public float audioRmsBaseline;
    public float audioRmsStdDev;
    public string[] jointKeys;
    public float[] jointX;
    public float[] jointY;
    public float[] jointZ;
    public float bodySwayBaseline;
}

[System.Serializable]
public class CalibrationAck
{
    public bool ok;
}

[System.Serializable]
class FaceCalibrationSample
{
    public float looking_away;
    public float mouth_moved;
    public string[] au_codes;
    public float[] au_values;
}