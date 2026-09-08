using System;
using System.IO;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.SceneManagement;
using WebSocketSharp;
using Windows.Kinect;

public class KinectAudioSender : MonoBehaviour
{
    [Header("WebSocket 設定")]
    public string sttUrl = "wss://api.re-memo.com/ws/stt";

    // MicController 設定此 callback 以接收 STT 回傳訊息（在 WS 背景執行緒呼叫）
    public System.Action<string> OnSttMessage;

    /// <summary>Kinect 麥克風即時音量（EMA 平滑），供情緒判斷使用。0 = 靜音，>0.015 通常為語音。</summary>
    public float CurrentAudioRms { get; private set; } = 0f;
    /// <summary>音高變異（Hz²，B 階段）。靜音或尚無足夠歷史時為 0。</summary>
    public float CurrentPitchVariance { get; private set; } = 0f;

    // 2026-09-08 稽核（實測心得回合沒收到語音）：KinectSensorSender 每 2 秒
    // 才送一次 audio_rms 給後端判斷 speaker_speaking，如果直接送當下那一瞬間
    // 的 CurrentAudioRms，長者講得快（尤其等很久才回一句很短的答案）很容易
    // 整句話都講完了、EMA 又衰減回底噪，剛好卡在兩次送出的中間，snapshot
    // 抓到的當下音量已經掉回去了——跟 UpdatePitch() 靠持續累積 15 筆歷史、
    // 不受單一瞬間快照影響是同一個問題的兩種呈現。改成記錄「自上次送出以來
    // 看過的最大值」，讓這 2 秒內只要有講話就會被抓到，不只看送出那一瞬間。
    private float _peakAudioRms = 0f;

    private WebSocket wsStt;

    // 斷線重連（見 ConnectWebSocket 內的 OnClose/OnError）：治療師的暫停/跳過/重播/
    // 繼續指令要靠這條連線送到 Unity，斷線後若不重連，按鈕會永久失效直到長者重開 App。
    private volatile bool needsReconnect = false;
    private float reconnectTimer = 0f;
    private bool isQuitting = false;
    private const float ReconnectDelay = 3f;

    private KinectSensor sensor;
    private AudioBeamFrameReader audioReader;
    private MemoryStream audioAccumulator = new MemoryStream();
    private const int CHUNK_SIZE = 3200;
    private bool isInitialized = false;
    private bool isSttActive = false;

    // wsStt.ConnectAsync() 是非同步的，TLS handshake 到 wss://api.re-memo.com 可能要幾百
    // ms～數秒；若這段期間就按下/放開麥克風，start/end 控制訊框過去是直接被
    // SendSttControl 的 ReadyState 檢查吞掉、永遠不會補送——這正是回合1（GameScene-1
    // 剛載入、連線才剛起步）常常「接不到 STT」的成因。原本只用一個 string 記最後一次
    // 沒送出去的訊框，若在連線Open前「按下又很快放開」，start會被後來的end直接蓋掉、
    // 憑空消失，後端從頭到尾收不到start、audio_buf沒清空基準，最終end來的時候
    // 音量不到門檻，連transcript都不會回——2026-08-21改成兩個獨立旗標，start跟end
    // 都能分別記住、OnOpen時依序（start先、end後）補送，不會互相蓋掉。
    private volatile bool _pendingStart = false;
    private volatile bool _pendingEnd = false;

    // Pitch detection（B 階段）
    private const int KINECT_AUDIO_SAMPLE_RATE = 16000;
    private const int PITCH_BUF_SIZE           = 512;   // 32ms @ 16 kHz
    private const int PITCH_HISTORY_SIZE        = 15;
    private readonly float[]      _pitchBuf     = new float[PITCH_BUF_SIZE];
    private int                   _pitchBufPos  = 0;
    private readonly Queue<float> _pitchHistory  = new Queue<float>();

    void Start()
    {
        // 暖身頁面只需要 PollAudio() 算的本地 CurrentAudioRms/CurrentPitchVariance
        // （KinectCalibrationManager 拿來建個人化音高門檻基準），用不到 STT，不建立
        // /ws/stt 連線；離開暖身進 InstructionScene/GameScene-1 後，各自場景的
        // KinectAudioSender 是獨立物件，Start() 會照常連線。
        if (SceneManager.GetActiveScene().name != "WarmupScene")
            ConnectWebSocket();
    }

    void ConnectWebSocket()
    {
        // session_id 讓後端 /session/{id}/control 知道要把治療師的重播/跳過/暫停/繼續
        // 指令轉發到哪一條連線（見 app/ws_registry.py）。跟 GameController 各自從
        // AuthSession 讀，不靠 GameController 賦值，避免兩個 MonoBehaviour 的
        // Start() 執行順序不保證先後而漏帶 session_id。
        string sessionId = AuthSession.SessionId ?? "";
        string url = string.IsNullOrEmpty(sessionId) ? sttUrl : $"{sttUrl}?session_id={sessionId}";
        wsStt = new WebSocket(AuthService.AppendToken(url));
        wsStt.SslConfiguration.EnabledSslProtocols = System.Security.Authentication.SslProtocols.Tls12;
        wsStt.OnOpen    += (s, e) =>
        {
            Debug.Log("[STT WS Kinect] 已連線");
            // 依序補送，start要先於end，順序對後端才有意義（見上面_pendingStart/
            // _pendingEnd宣告處的說明）。
            if (_pendingStart)
            {
                _pendingStart = false;
                wsStt.SendAsync("{\"type\":\"start\"}", null);
            }
            if (_pendingEnd)
            {
                _pendingEnd = false;
                wsStt.SendAsync("{\"type\":\"end\"}", null);
            }
        };
        wsStt.OnError   += (s, e) => { Debug.LogError($"[STT WS Kinect] 錯誤: {e.Message}"); needsReconnect = true; };
        wsStt.OnClose   += (s, e) => { Debug.Log("[STT WS Kinect] 已關閉"); if (!isQuitting) needsReconnect = true; };
        wsStt.OnMessage += (s, e) => { if (e.IsText) OnSttMessage?.Invoke(e.Data); };
        wsStt.ConnectAsync();
    }

    /// <summary>
    /// KinectSensorSender 每 sendInterval（2秒）呼叫一次，取「這段時間內看過
    /// 的最大音量」送給後端判斷 speaker_speaking，取代直接送當下那一瞬間的
    /// CurrentAudioRms（見 _peakAudioRms 宣告處的說明）。重置成目前值而不是
    /// 0，是因為講話可能剛好橫跨這次呼叫的當下還沒停，歸零的話下一輪的峰值
    /// 會從 0 開始爬，等於把還在進行中的這段語音音量憑空砍掉一截。
    /// </summary>
    public float ConsumePeakAudioRms()
    {
        float peak = _peakAudioRms;
        _peakAudioRms = CurrentAudioRms;
        return peak;
    }

    // ── 公開 API，讓 MicController 在按下/放開麥克風時呼叫 ──

    public void StartSTT()
    {
        isSttActive = true;
        audioAccumulator.SetLength(0);
        SendSttControl("start");
        Debug.Log("[KinectAudio] StartSTT");
    }

    public void StopSTT()
    {
        isSttActive = false;
        SendSttControl("end");
        Debug.Log("[KinectAudio] StopSTT");
    }

    private void SendSttControl(string type)
    {
        if (wsStt?.ReadyState == WebSocketState.Open)
        {
            wsStt.SendAsync($"{{\"type\":\"{type}\"}}", null);
        }
        else if (type == "start")
        {
            _pendingStart = true;
        }
        else if (type == "end")
        {
            _pendingEnd = true;
        }
    }

    private void TryInitAudio()
    {
        if (isInitialized) return;

        KinectManager km = KinectManager.Instance;
        if (km == null || !km.IsInitialized()) return;

#if UNITY_STANDALONE_WIN
        Kinect2Interface sensorInterface = km.GetSensorData().sensorInterface as Kinect2Interface;
        sensor = sensorInterface?.kinectSensor;
#endif
        if (sensor == null) { isInitialized = true; return; }

        audioReader = sensor.AudioSource.OpenReader();
        if (audioReader == null) { isInitialized = true; return; }

        var audioBeams = sensor.AudioSource.AudioBeams;
        if (audioBeams != null && audioBeams.Count > 0)
            audioBeams[0].AudioBeamMode = AudioBeamMode.Automatic;

        isInitialized = true;
        Debug.Log("[Audio] 初始化成功");
    }

    void Update()
    {
        TryInitAudio();
        PollAudio();
        TickReconnect();
        TickKeepAlive();
    }

    void TickReconnect()
    {
        if (!needsReconnect) return;
        reconnectTimer += Time.deltaTime;
        if (reconnectTimer < ReconnectDelay) return;
        reconnectTimer = 0f;
        needsReconnect = false;
        Debug.Log("[STT WS Kinect] 嘗試重新連線");
        ConnectWebSocket();
    }

    // 回合間（尤其是答完生圖前引導問題、後端在跑 LLM/生圖那段）長者不會碰麥克風，
    // 這條 STT 連線會閒置一段不確定的時間——太久沒有任何訊框，中間的伺服器/代理
    // 容易把連線判定逾時關掉。這裡固定週期送一個後端會忽略的輕量心跳文字訊框，
    // 讓連線一直有動靜，從源頭避免被判定逾時，不用等斷線後才靠 TickReconnect 補救。
    private float keepAliveTimer = 0f;
    private const float KeepAliveInterval = 20f;

    void TickKeepAlive()
    {
        if (wsStt == null || wsStt.ReadyState != WebSocketState.Open) { keepAliveTimer = 0f; return; }
        keepAliveTimer += Time.deltaTime;
        if (keepAliveTimer < KeepAliveInterval) return;
        keepAliveTimer = 0f;
        wsStt.SendAsync("{\"type\":\"ping\"}", null);
    }

    private void PollAudio()
    {
        if (audioReader == null) return;

        var frameList = audioReader.AcquireLatestBeamFrames();
        if (frameList == null) return;

        foreach (AudioBeamFrame frame in frameList)
        {
            if (frame?.SubFrames == null) continue;
            foreach (AudioBeamSubFrame subFrame in frame.SubFrames)
            {
                if (subFrame == null) continue;
                uint frameBytes = subFrame.FrameLengthInBytes;
                if (frameBytes == 0) continue;

                byte[] floatBuffer = new byte[frameBytes];
                subFrame.CopyFrameDataToArray(floatBuffer);

                CurrentAudioRms = Mathf.Lerp(CurrentAudioRms, ComputeRms(floatBuffer), 0.4f);
                if (CurrentAudioRms > _peakAudioRms) _peakAudioRms = CurrentAudioRms;

                // B 階段：音高偵測樣本累積
                int sCount = (int)(frameBytes / 4);
                for (int i = 0; i < sCount; i++)
                {
                    _pitchBuf[_pitchBufPos++] = BitConverter.ToSingle(floatBuffer, i * 4);
                    if (_pitchBufPos >= PITCH_BUF_SIZE)
                    {
                        UpdatePitch(_pitchBuf);
                        _pitchBufPos = 0;
                    }
                }

                byte[] int16Buffer = ConvertFloat32ToInt16(floatBuffer);

                audioAccumulator.Write(int16Buffer, 0, int16Buffer.Length);
                if (audioAccumulator.Length >= CHUNK_SIZE)
                {
                    // 只有「沒在錄音、而且也沒有還沒送出去的start/end」才是真的能丟棄的
                    // 雜訊——如果按下麥克風又在連線Open前很快放開，isSttActive這時已經
                    // 變false，但_pendingStart/_pendingEnd還在等OnOpen補送，這段音訊
                    // 其實是剛剛那次錄音的內容，不能因為旗標已經翻回false就丟掉，否則
                    // 後端收到的start/end之間完全沒有音訊，比門檻不夠、連transcript都
                    // 不會回（見 _pendingStart 宣告處的說明）。
                    if (!isSttActive && !_pendingStart && !_pendingEnd)
                    {
                        audioAccumulator.SetLength(0);
                    }
                    else if (wsStt?.ReadyState == WebSocketState.Open)
                    {
                        wsStt.SendAsync(audioAccumulator.ToArray(), null);
                        audioAccumulator.SetLength(0);
                    }
                    // else：連線還沒 Open，保留累積的音訊，等連線一 Open、OnOpen 補送完
                    // start/end 之後，下一輪 PollAudio 會落到上面「已經 Open」那個分支
                    // 把這段音訊送出去，不會漏字。
                }
            }
            frame.Dispose();
        }
    }

    private float ComputeRms(byte[] float32Bytes)
    {
        int count = float32Bytes.Length / 4;
        if (count == 0) return 0f;
        float sumSq = 0f;
        for (int i = 0; i < count; i++)
        {
            float s = BitConverter.ToSingle(float32Bytes, i * 4);
            sumSq += s * s;
        }
        return Mathf.Sqrt(sumSq / count);
    }

    private byte[] ConvertFloat32ToInt16(byte[] float32Bytes)
    {
        int sampleCount = float32Bytes.Length / 4;
        byte[] result = new byte[sampleCount * 2];
        for (int i = 0; i < sampleCount; i++)
        {
            float sample = BitConverter.ToSingle(float32Bytes, i * 4);
            sample = Mathf.Clamp(sample, -1f, 1f);
            short s = (short)(sample * 32767);
            byte[] b = BitConverter.GetBytes(s);
            result[i * 2]     = b[0];
            result[i * 2 + 1] = b[1];
        }
        return result;
    }

    private void UpdatePitch(float[] samples)
    {
        if (CurrentAudioRms < 0.005f) return;  // 靜音跳過

        int minPeriod = KINECT_AUDIO_SAMPLE_RATE / 400;  // 40 samples（上限 400 Hz）
        int maxPeriod = KINECT_AUDIO_SAMPLE_RATE / 80;   // 200 samples（下限 80 Hz）

        float maxCorr    = float.MinValue;
        int   bestPeriod = maxPeriod;
        for (int period = minPeriod; period <= maxPeriod; period++)
        {
            float corr = 0f;
            int   len  = samples.Length - period;
            for (int i = 0; i < len; i++)
                corr += samples[i] * samples[i + period];
            if (corr > maxCorr) { maxCorr = corr; bestPeriod = period; }
        }

        float pitch = (float)KINECT_AUDIO_SAMPLE_RATE / bestPeriod;
        _pitchHistory.Enqueue(pitch);
        if (_pitchHistory.Count > PITCH_HISTORY_SIZE) _pitchHistory.Dequeue();

        float mean = 0f;
        foreach (var p in _pitchHistory) mean += p;
        mean /= _pitchHistory.Count;
        float variance = 0f;
        foreach (var p in _pitchHistory) variance += (p - mean) * (p - mean);
        CurrentPitchVariance = variance / _pitchHistory.Count;
    }

    void OnDestroy()
    {
        isQuitting = true;
        audioReader?.Dispose();
        audioReader = null;
        audioAccumulator?.Dispose();
        wsStt?.Close();
    }
}
