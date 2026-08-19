using System;
using System.IO;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.SceneManagement;
using WebSocketSharp;
using Windows.Kinect;

public class KinectAudioSender : MonoBehaviour
{
    // 跨場景 singleton：STT WebSocket 的連線耗時（見 ConnectWebSocket）遠比單一場景的
    // 生命週期長，若每個場景各自建立/銷毀一份，長者一進場景就按麥克風會撞上「還沒連上」
    // 的空窗期（2026-08 稽核已實測到）。改成從 WarmupScene 建立、靠 DontDestroyOnLoad
    // 撐過 InstructionScene／GameScene-1／LoadingScene／ShareScene 這整段流程，直到
    // 流程外的場景（ThankYouScene 等）載入才自我銷毀、關閉連線，下個病患的 WarmupScene
    // 再重新建一份、換新的 session_id。
    public static KinectAudioSender Instance { get; private set; }

    private static readonly HashSet<string> KeepAliveScenes = new HashSet<string>
    {
        "WarmupScene", "InstructionScene", "GameScene-1", "LoadingScene", "ShareScene",
    };

    [Header("WebSocket 設定")]
    public string sttUrl = "wss://api.re-memo.com/ws/stt";

    // MicController 設定此 callback 以接收 STT 回傳訊息（在 WS 背景執行緒呼叫）
    public System.Action<string> OnSttMessage;

    /// <summary>Kinect 麥克風即時音量（EMA 平滑），供情緒判斷使用。0 = 靜音，>0.015 通常為語音。</summary>
    public float CurrentAudioRms { get; private set; } = 0f;
    /// <summary>音高變異（Hz²，B 階段）。靜音或尚無足夠歷史時為 0。</summary>
    public float CurrentPitchVariance { get; private set; } = 0f;

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

    // Pitch detection（B 階段）
    private const int KINECT_AUDIO_SAMPLE_RATE = 16000;
    private const int PITCH_BUF_SIZE           = 512;   // 32ms @ 16 kHz
    private const int PITCH_HISTORY_SIZE        = 15;
    private readonly float[]      _pitchBuf     = new float[PITCH_BUF_SIZE];
    private int                   _pitchBufPos  = 0;
    private readonly Queue<float> _pitchHistory  = new Queue<float>();

    void Awake()
    {
        // 暫時診斷用 log（見 2026-08 稽核：singleton 上線後後端仍觀察到同一 session_id
        // 開出兩條 /ws/stt，需要直接在 Console 對照 InstanceID 才能判斷是「兩個物件都
        // 活下來」還是別的原因）——確認修好後可以拿掉。
        Debug.Log($"[KinectAudioSender] Awake @ scene={SceneManager.GetActiveScene().name}, thisID={GetInstanceID()}, existingInstanceID={(Instance == null ? "null" : Instance.GetInstanceID().ToString())}");

        if (Instance != null && Instance != this)
        {
            // 場景裡自己拖進去的那份是重複的舊寫法殘留：這裡自我銷毀，Start() 就
            // 不會再跑第二次，不會開出第二條 STT WebSocket。GameController 等消費端
            // 的 Inspector 欄位會在 Destroy 後變成 Unity 的 fake-null，靠各自 Start()
            // 裡的 KinectAudioSender.Instance 備援取得真正活著的那份。
            Debug.Log($"[KinectAudioSender] Awake: 判定為重複物件，自我銷毀 (thisID={GetInstanceID()})");
            Destroy(gameObject);
            return;
        }
        Instance = this;
        DontDestroyOnLoad(gameObject);
        SceneManager.sceneLoaded += OnSceneLoaded;
        Debug.Log($"[KinectAudioSender] Awake: 設為 singleton 並 DontDestroyOnLoad (thisID={GetInstanceID()})");
    }

    void Start()
    {
        Debug.Log($"[KinectAudioSender] Start → ConnectWebSocket (thisID={GetInstanceID()}, scene={SceneManager.GetActiveScene().name})");
        ConnectWebSocket();
    }

    private void OnSceneLoaded(Scene scene, LoadSceneMode mode)
    {
        // 離開整段「校正→說明→遊戲→分享」流程（例如轉場到 ThankYouScene）就代表這場
        // 療程結束，銷毀自己、關掉連線；下個病患進 WarmupScene 時 Awake() 會重新建一份。
        Debug.Log($"[KinectAudioSender] OnSceneLoaded: scene={scene.name}, inKeepAlive={KeepAliveScenes.Contains(scene.name)}, thisID={GetInstanceID()}");
        if (!KeepAliveScenes.Contains(scene.name))
        {
            Debug.Log($"[KinectAudioSender] 離開流程場景，自我銷毀 (thisID={GetInstanceID()})");
            Destroy(gameObject);
        }
    }

    void ConnectWebSocket()
    {
        // session_id 讓後端 /session/{id}/control 知道要把治療師的重播/跳過/暫停/繼續
        // 指令轉發到哪一條連線（見 app/ws_registry.py）。跟 GameController 各自從
        // PlayerPrefs 讀，不靠 GameController 賦值，避免兩個 MonoBehaviour 的
        // Start() 執行順序不保證先後而漏帶 session_id。
        string sessionId = PlayerPrefs.GetString("session_id", "");
        Debug.Log($"[KinectAudioSender] ConnectWebSocket 進入點 (thisID={GetInstanceID()}, session_id={sessionId}, frameCount={Time.frameCount})");
        string url = string.IsNullOrEmpty(sessionId) ? sttUrl : $"{sttUrl}?session_id={sessionId}";
        wsStt = new WebSocket(AuthService.AppendToken(url));
        wsStt.SslConfiguration.EnabledSslProtocols = System.Security.Authentication.SslProtocols.Tls12;
        wsStt.OnOpen    += (s, e) => Debug.Log("[STT WS Kinect] 已連線");
        wsStt.OnError   += (s, e) => { Debug.LogError($"[STT WS Kinect] 錯誤: {e.Message}"); needsReconnect = true; };
        wsStt.OnClose   += (s, e) => { Debug.Log("[STT WS Kinect] 已關閉"); if (!isQuitting) needsReconnect = true; };
        wsStt.OnMessage += (s, e) => { if (e.IsText) OnSttMessage?.Invoke(e.Data); };
        wsStt.ConnectAsync();
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
            wsStt.SendAsync($"{{\"type\":\"{type}\"}}", null);
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
                    if (isSttActive && wsStt?.ReadyState == WebSocketState.Open)
                        wsStt.SendAsync(audioAccumulator.ToArray(), null);

                    audioAccumulator.SetLength(0);
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
        Debug.Log($"[KinectAudioSender] OnDestroy (thisID={GetInstanceID()}, wasInstance={Instance == this})");
        SceneManager.sceneLoaded -= OnSceneLoaded;
        if (Instance == this) Instance = null;
        isQuitting = true;
        audioReader?.Dispose();
        audioReader = null;
        audioAccumulator?.Dispose();
        wsStt?.Close();
    }
}
