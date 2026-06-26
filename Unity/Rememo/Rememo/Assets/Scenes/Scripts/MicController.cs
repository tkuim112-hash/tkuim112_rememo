using UnityEngine;
using UnityEngine.UI;
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

    [Header("WebSocket 設定")]
    public string serverUrl = "ws://localhost:8000/ws/stt";

    [Header("錄音設定")]
    public int sampleRate = 16000;
    public int maxRecordSeconds = 60;

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

    private string placeholderText = "想到什麼就說什麼，按下麥克風可以用說的…";

    private string[] fakeResponses = {
        "美麗阿姨，這幅畫裡的紅瓦房跟老家好像！看著看著，仿佛又聽見那個夏天熱鬧的蟬鳴聲。",
        "阿公，您說的紡織廠讓我想起了那個年代，大家一起打拼的日子真的很珍貴。",
        "聽您說起淡水的往事，那個年代的台灣真的很美，充滿了人情味。",
        "謝謝您分享這段回憶，這些故事會永遠留在我們心裡。"
    };

    [System.Serializable]
    private class ControlPayload
    {
        public string type;
    }

    [System.Serializable]
    private class STTMessage
    {
        public string type;
        public string text;
        public bool isFinal;
    }

    void Start()
    {
        micButton.onClick.AddListener(OnMicButtonClick);
        ResetInputText();
        ConnectWebSocket();
    }

    void ConnectWebSocket()
    {
        ws = new WebSocket(serverUrl);
        ws.OnOpen += (s, e) => Debug.Log("[STT WS] 已連線");
        ws.OnError += (s, e) => Debug.LogError($"[STT WS] 錯誤: {e.Message}");
        ws.OnClose += (s, e) => Debug.Log("[STT WS] 已關閉");
        ws.OnMessage += OnWebSocketMessage;
        ws.ConnectAsync();
    }

    // 在 websocket-sharp 的背景執行緒上呼叫，不能直接動 UI，先進佇列由 Update 處理
    private void OnWebSocketMessage(object sender, MessageEventArgs e)
    {
        if (!e.IsText) return;
        lock (queueLock)
        {
            incomingMessages.Enqueue(e.Data);
        }
    }

    void ResetInputText()
    {
        if (inputText == null) return;
        inputText.text = placeholderText;
        inputText.color = new Color(0.67f, 0.67f, 0.67f, 1f); // 灰色
    }

    void OnMicButtonClick()
    {
        if (!isRecording)
            StartRecording();
        else
            StopRecording();
    }

    void StartRecording()
    {
        if (Microphone.devices.Length == 0)
        {
            Debug.LogWarning("[Mic] 找不到麥克風裝置");
            return;
        }

        if (ws == null || ws.ReadyState != WebSocketState.Open)
            ConnectWebSocket();

        micDevice = Microphone.devices[0];
        micClip = Microphone.Start(micDevice, true, maxRecordSeconds, sampleRate);
        lastSamplePos = 0;
        isRecording = true;

        SendControl("start");

        inputText.text = "錄音中...";
        inputText.color = new Color(1f, 0.4f, 0.4f, 1f); // 紅色
        micButtonImage.color = new Color(1f, 0.3f, 0.3f, 1f); // 按鈕變紅
    }

    void StopRecording()
    {
        isRecording = false;
        Microphone.End(micDevice);

        SendControl("end");

        inputText.text = "辨識中...";
        inputText.color = new Color(0.2f, 0.2f, 0.2f, 1f);
        micButtonImage.color = Color.white;
    }

    void SendControl(string type)
    {
        if (ws?.ReadyState == WebSocketState.Open)
            ws.SendAsync(JsonUtility.ToJson(new ControlPayload { type = type }), null);
    }

    void Update()
    {
        if (isRecording)
            StreamMicAudio();

        DrainIncomingMessages();
    }

    void StreamMicAudio()
    {
        int pos = Microphone.GetPosition(micDevice);
        if (pos < lastSamplePos) lastSamplePos = 0; // 緩衝區繞回起點，捨棄這小段避免讀到錯誤位置
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
            result[i * 2] = (byte)(s & 0xFF);
            result[i * 2 + 1] = (byte)((s >> 8) & 0xFF);
        }
        return result;
    }

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

    void HandleSTTMessage(string json)
    {
        STTMessage msg;
        try { msg = JsonUtility.FromJson<STTMessage>(json); }
        catch { Debug.LogWarning("[STT] 無法解析訊息: " + json); return; }

        if (msg == null || msg.type != "transcript") return;

        inputText.text = msg.text;
        inputText.color = new Color(0.2f, 0.2f, 0.2f, 1f);

        if (msg.isFinal)
            OnFinalTranscript();
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
        if (isRecording)
            Microphone.End(micDevice);
        ws?.Close();
    }
}