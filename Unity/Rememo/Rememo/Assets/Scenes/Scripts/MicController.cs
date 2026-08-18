using UnityEngine;
using UnityEngine.UI;
using System.Collections;
using System.Collections.Generic;
using TMPro;
using WebSocketSharp;

public class MicController : MonoBehaviour
{
    [Header("UI 元件")]
    public Button micButton;
    public Image micButtonImage;
    public TMP_Text aiText;
    public Image sceneImage;
    public TMP_Text inputText;

    [Header("Kinect 整合")]
    [Tooltip("拖入場景中的 KinectAudioSender；若留空則退回使用內建麥克風")]
    public KinectAudioSender kinectAudioSender;

    [Header("WebSocket 設定（內建麥克風模式用）")]
    public string serverUrl = "wss://api.re-memo.com/ws/stt";

    [Header("錄音設定（內建麥克風模式用）")]
    public int sampleRate = 16000;
    public int maxRecordSeconds = 60;

    [Header("逐字動畫")]
    [Tooltip("每個字之間的秒數，建議 0.02~0.05")]
    public float charInterval = 0.03f;

    [Header("假資料")]
    public Sprite[] testImages;

    private WebSocket ws;
    private AudioClip micClip;
    private string micDevice;
    private int lastSamplePos = 0;
    private bool isRecording = false;
    private int responseIndex = 0;

    private readonly Queue<string> incomingMessages = new Queue<string>();
    private readonly object queueLock = new object();

    private readonly string placeholderText = "想到什麼就說什麼，按下麥克風可以用說的…";
    private string displayedText = "";   // 動畫目前打到的文字
    private Coroutine typingCoroutine;

    private string[] fakeResponses = {
        "美麗阿姨，這幅畫裡的紅瓦房跟老家好像！看著看著，仿佛又聽見那個夏天熱鬧的蟬鳴聲。",
        "阿公，您說的紡織廠讓我想起了那個年代，大家一起打拼的日子真的很珍貴。",
        "聽您說起淡水的往事，那個年代的台灣真的很美，充滿了人情味。",
        "謝謝您分享這段回憶，這些故事會永遠留在我們心裡。"
    };

    [System.Serializable]
    private class ControlPayload { public string type; }

    [System.Serializable]
    private class STTMessage { public string type; public string text; public bool isFinal; }

    // ── 是否使用 Kinect 音訊 ──
    private bool UseKinect => kinectAudioSender != null;

    void Start()
    {
        micButton.onClick.AddListener(OnMicButtonClick);
        ResetInputText();

        // 只有在「內建麥克風模式」才自己建 WebSocket
        if (!UseKinect)
            ConnectWebSocket();
    }

    // ── 內建麥克風模式的 WebSocket ──

    void ConnectWebSocket()
    {
        ws = new WebSocket(AuthService.AppendToken(serverUrl));
        ws.SslConfiguration.EnabledSslProtocols = System.Security.Authentication.SslProtocols.Tls12;
        ws.OnOpen  += (s, e) => Debug.Log("[STT WS] 已連線");
        ws.OnError += (s, e) => Debug.LogError($"[STT WS] 錯誤: {e.Message}");
        ws.OnClose += (s, e) => Debug.Log("[STT WS] 已關閉");
        ws.OnMessage += (s, e) => {
            if (!e.IsText) return;
            lock (queueLock) incomingMessages.Enqueue(e.Data);
        };
        ws.ConnectAsync();
    }

    // ── UI helpers ──

    void ResetInputText()
    {
        if (inputText == null) return;
        inputText.text = placeholderText;
        inputText.color = new Color(0.67f, 0.67f, 0.67f, 1f);
        displayedText = "";
    }

    // ── 按鈕 ──

    void OnMicButtonClick()
    {
        if (!isRecording) StartRecording();
        else              StopRecording();
    }

    void StartRecording()
    {
        isRecording = true;

        if (UseKinect)
        {
            // Kinect 模式：讓 KinectAudioSender 負責錄音並送 STT WebSocket
            // 訂閱回傳訊息（Kinect 的 wsStt.OnMessage）
            kinectAudioSender.OnSttMessage = OnKinectSttMessage;
            kinectAudioSender.StartSTT();
        }
        else
        {
            // 內建麥克風模式
            if (Microphone.devices.Length == 0)
            {
                Debug.LogWarning("[Mic] 找不到麥克風裝置");
                isRecording = false;
                return;
            }
            if (ws == null || ws.ReadyState != WebSocketState.Open)
                ConnectWebSocket();

            micDevice = Microphone.devices[0];
            micClip   = Microphone.Start(micDevice, true, maxRecordSeconds, sampleRate);
            lastSamplePos = 0;
            SendControl("start");
        }

        // 重置逐字動畫狀態，避免上次錄音的 displayedText 干擾新一輪
        if (typingCoroutine != null) StopCoroutine(typingCoroutine);
        displayedText   = "";
        inputText.text  = "錄音中...";
        inputText.color = new Color(1f, 0.4f, 0.4f, 1f);
        micButtonImage.color = new Color(1f, 0.3f, 0.3f, 1f);
    }

    void StopRecording()
    {
        isRecording = false;

        if (UseKinect)
        {
            kinectAudioSender.StopSTT();
        }
        else
        {
            Microphone.End(micDevice);
            SendControl("end");
        }

        inputText.text  = "辨識中...";
        inputText.color = new Color(0.2f, 0.2f, 0.2f, 1f);
        micButtonImage.color = Color.white;
    }

    // ── 內建麥克風模式：控制訊息 / 音訊串流 ──

    void SendControl(string type)
    {
        if (ws?.ReadyState == WebSocketState.Open)
            ws.SendAsync(JsonUtility.ToJson(new ControlPayload { type = type }), null);
    }

    void Update()
    {
        if (!UseKinect && isRecording)
            StreamMicAudio();

        DrainIncomingMessages();
    }

    void StreamMicAudio()
    {
        int pos = Microphone.GetPosition(micDevice);
        if (pos < lastSamplePos) lastSamplePos = 0;
        int sampleCount = pos - lastSamplePos;
        if (sampleCount <= 0) return;

        float[] samples = new float[sampleCount];
        micClip.GetData(samples, lastSamplePos);
        lastSamplePos = pos;

        if (ws?.ReadyState == WebSocketState.Open)
            ws.SendAsync(FloatToInt16Bytes(samples), null);
    }

    byte[] FloatToInt16Bytes(float[] samples)
    {
        byte[] result = new byte[samples.Length * 2];
        for (int i = 0; i < samples.Length; i++)
        {
            short s = (short)(Mathf.Clamp(samples[i], -1f, 1f) * 32767);
            result[i * 2]     = (byte)(s & 0xFF);
            result[i * 2 + 1] = (byte)((s >> 8) & 0xFF);
        }
        return result;
    }

    // ── 訊息佇列（內建麥克風模式） ──

    void DrainIncomingMessages()
    {
        while (true)
        {
            string json;
            lock (queueLock)
            {
                if (incomingMessages.Count == 0) return;
                json = incomingMessages.Dequeue();
            }
            HandleSTTMessage(json);
        }
    }

    // ── Kinect 模式：KinectAudioSender 透過 callback 送回訊息 ──

    private void OnKinectSttMessage(string json)
    {
        // 在 websocket-sharp 背景執行緒呼叫，先進佇列再交給 Update 處理
        lock (queueLock) incomingMessages.Enqueue(json);
    }

    // ── 核心：處理 STT 回傳 ──

    void HandleSTTMessage(string json)
    {
        STTMessage msg;
        try { msg = JsonUtility.FromJson<STTMessage>(json); }
        catch { Debug.LogWarning("[STT] 無法解析: " + json); return; }

        if (msg == null || msg.type != "transcript") return;

        // 逐字動畫顯示辨識結果
        if (typingCoroutine != null)
            StopCoroutine(typingCoroutine);
        typingCoroutine = StartCoroutine(TypeCharByChar(msg.text));

        if (msg.isFinal)
            OnFinalTranscript();
    }

    // ── 逐字打字動畫（像 Google 語音輸入） ──

    IEnumerator TypeCharByChar(string target)
    {
        inputText.color = new Color(0.2f, 0.2f, 0.2f, 1f);

        // 若 target 是 displayedText 的延伸，只打出新增的部分
        if (target.StartsWith(displayedText))
        {
            for (int i = displayedText.Length; i <= target.Length; i++)
            {
                string partial = target.Substring(0, i);
                inputText.text = partial;
                displayedText  = partial;
                yield return new WaitForSeconds(charInterval);
            }
        }
        else
        {
            // 文字差異較大（interim 結果改寫），直接替換
            inputText.text = target;
            displayedText  = target;
        }
    }

    void OnFinalTranscript()
    {
        int index = responseIndex % fakeResponses.Length;
        aiText.text = fakeResponses[index];

        if (testImages != null && testImages.Length > 0)
            sceneImage.sprite = testImages[index % testImages.Length];

        responseIndex++;
    }

    void OnDestroy()
    {
        if (!UseKinect && isRecording)
            Microphone.End(micDevice);
        ws?.Close();
    }
}
