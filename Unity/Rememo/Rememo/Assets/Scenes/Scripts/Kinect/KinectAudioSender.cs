using System;
using System.IO;
using System.Collections.Generic;
using UnityEngine;
using WebSocketSharp;
using Windows.Kinect;

public class KinectAudioSender : MonoBehaviour
{
    [Header("WebSocket 設定")]
    public string sttUrl = "ws://localhost:8000/ws/stt";

    // MicController 設定此 callback 以接收 STT 回傳訊息（在 WS 背景執行緒呼叫）
    public System.Action<string> OnSttMessage;

    /// <summary>Kinect 麥克風即時音量（EMA 平滑），供情緒判斷使用。0 = 靜音，>0.015 通常為語音。</summary>
    public float CurrentAudioRms { get; private set; } = 0f;
    /// <summary>音高變異（Hz²，B 階段）。靜音或尚無足夠歷史時為 0。</summary>
    public float CurrentPitchVariance { get; private set; } = 0f;

    private WebSocket wsStt;

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

    void Start()
    {
        // session_id 讓後端 /session/{id}/control 知道要把治療師的重播/跳過/暫停/繼續
        // 指令轉發到哪一條連線（見 app/ws_registry.py）。跟 GameController 各自從
        // PlayerPrefs 讀，不靠 GameController 賦值，避免兩個 MonoBehaviour 的
        // Start() 執行順序不保證先後而漏帶 session_id。
        string sessionId = PlayerPrefs.GetString("session_id", "");
        string url = string.IsNullOrEmpty(sessionId) ? sttUrl : $"{sttUrl}?session_id={sessionId}";
        wsStt = new WebSocket(AuthService.AppendToken(url));
        wsStt.OnOpen    += (s, e) => Debug.Log("[STT WS Kinect] 已連線");
        wsStt.OnError   += (s, e) => Debug.LogError($"[STT WS Kinect] 錯誤: {e.Message}");
        wsStt.OnClose   += (s, e) => Debug.Log("[STT WS Kinect] 已關閉");
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
        audioReader?.Dispose();
        audioReader = null;
        audioAccumulator?.Dispose();
        wsStt?.Close();
    }
}
