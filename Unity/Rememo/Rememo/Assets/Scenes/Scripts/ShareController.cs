using System.Text;
using UnityEngine;
using UnityEngine.UI;
using UnityEngine.Networking;
using UnityEngine.SceneManagement;
using TMPro;
using System.Collections;
using System.Collections.Generic;
using WebSocketSharp;

public class ShareController : MonoBehaviour
{
    [Header("UI 元件")]
    public Button submitButton;
    public Button micButton;
    public Button replayButton;
    public Image micButtonImage;
    public TMP_Text inputText;
    public TMP_Text closingText;    // 線條區：顯示 LLM 收尾語
    [Tooltip("心得環節開場語音（承接語／感謝語／問句，見 LoadClosingText）播放用")]
    public AudioSource audioSource;

    [Header("Kinect 整合")]
    [Tooltip("拖入場景中的 KinectAudioSender；若留空則退回使用內建麥克風")]
    public KinectAudioSender kinectAudioSender;
    [Tooltip("拖入場景中的 KinectSensorSender；供分享階段情緒追蹤及反應時間計算")]
    public KinectSensorSender kinectSensorSender;

    [Header("後端設定")]
    public string backendUrl = "https://api.re-memo.com";

    [Header("WebSocket 設定（內建麥克風模式用）")]
    public string serverUrl = "wss://api.re-memo.com/ws/stt";

    [Header("錄音設定（內建麥克風模式用）")]
    public int sampleRate = 16000;
    public int maxRecordSeconds = 60;

    [Header("逐字動畫")]
    [Tooltip("每個字之間的秒數，建議 0.02~0.05")]
    public float charInterval = 0.03f;

    [Header("心得環節收尾")]
    [Tooltip("長者送出心得後，畫面顯示承接語＋收尾肯定語的秒數，之後才轉場到 ThankYouScene")]
    public float closingMessageDisplaySeconds = 6f;

    private WebSocket ws;
    private AudioClip micClip;
    private string micDevice;
    private int lastSamplePos = 0;
    private bool isRecording = false;

    private readonly Queue<string> incomingMessages = new Queue<string>();
    private readonly object queueLock = new object();

    private readonly string placeholderText = "想到什麼就說什麼，按下麥克風可以用說的…";
    private string displayedText = "";
    private string closingFullText = "";
    private List<string> closingAudioUris;
    private Coroutine typingCoroutine;
    private Coroutine replayCoroutine;
    private bool isWaitingForStt = false;
    private bool isPaused = false;
    private Coroutine sttTimeoutCoroutine;
    private readonly WaitForSeconds sttTimeoutWait = new WaitForSeconds(5f);

    [System.Serializable]
    private class ControlPayload { public string type; }

    [System.Serializable]
    private class STTMessage { public string type; public string text; public bool isFinal; public string action; }

    [System.Serializable]
    private class ClosingResponse { public bool ok; public string closing_message; }

    [System.Serializable]
    private class TextPayload { public string text; }

    private bool UseKinect => kinectAudioSender != null;

    void Start()
    {
        submitButton.onClick.AddListener(OnSubmit);
        micButton.onClick.AddListener(OnMicToggle);
        replayButton.onClick.AddListener(OnReplay);
        ResetInputText();
        LoadClosingText();

        if (UseKinect)
            // 掛在 Start() 而不是 StartRecording()：治療師端的暫停/繼續/重播/結束
            // 指令隨時可能在長者第一次按麥克風之前就送到，這裡要先掛好才不會漏接
            // （比照 GameController.cs 的作法）。
            kinectAudioSender.OnSttMessage = OnKinectSttMessage;
        else
            ConnectWebSocket();
    }

    void LoadClosingText()
    {
        if (closingText == null) return;
        string text = PlayerPrefs.GetString("ClosingText", "");
        string thanks = PlayerPrefs.GetString("ClosingThanks", "");
        string question = PlayerPrefs.GetString("ClosingQuestion", "");
        // 三段（承接語／感謝語／問題）各自可能是空字串（例如心得環節開場
        // 邀請語只有 question，沒有 text/thanks），只把有內容的段落接起來，
        // 避免空字串還是接了一個換行、畫面上多出空行。
        var segments = new List<string>();
        if (!string.IsNullOrEmpty(text)) segments.Add(text);
        if (!string.IsNullOrEmpty(thanks)) segments.Add(thanks);
        if (!string.IsNullOrEmpty(question)) segments.Add(question);
        closingFullText = string.Join("\n", segments);
        closingText.text = closingFullText;
        // 收尾問題顯示完畢 → 啟動反應時間計時
        kinectSensorSender?.OnQuestionAsked();

        // 承接語／感謝語／問句三段都是前端內建預錄音檔（見 audio_bank.py，
        // GameController.SendResponse 存進 PlayerPrefs 時已經用 '|' 串好），
        // 依序接起來播——這裡沒有需要即時TTS的動態內容，全部是本地 key，
        // 不用像 GameController 那樣還要組後端下載的 audio_path。
        closingAudioUris = new List<string>();
        closingAudioUris.AddRange(LocalAudioPlayer.BuildUris(null, SplitAudioKeys("ClosingSceneAudioKeys")));
        closingAudioUris.AddRange(LocalAudioPlayer.BuildUris(null, SplitAudioKeys("ClosingThanksAudioKeys")));
        closingAudioUris.AddRange(LocalAudioPlayer.BuildUris(null, SplitAudioKeys("ClosingQuestionAudioKeys")));
        if (closingAudioUris.Count > 0)
            StartCoroutine(LocalAudioPlayer.PlaySequence(audioSource, closingAudioUris));
    }

    static string[] SplitAudioKeys(string playerPrefsKey)
    {
        string joined = PlayerPrefs.GetString(playerPrefsKey, "");
        return string.IsNullOrEmpty(joined) ? System.Array.Empty<string>() : joined.Split('|');
    }

    void OnReplay()
    {
        if (string.IsNullOrEmpty(closingFullText)) return;
        if (replayCoroutine != null) StopCoroutine(replayCoroutine);
        closingText.text = "";
        replayCoroutine = StartCoroutine(TypeClosingText(closingFullText));
        if (closingAudioUris != null && closingAudioUris.Count > 0)
            StartCoroutine(LocalAudioPlayer.PlaySequence(audioSource, closingAudioUris));
    }

    IEnumerator TypeClosingText(string target)
    {
        for (int i = 0; i <= target.Length; i++)
        {
            closingText.text = target.Substring(0, i);
            yield return new WaitForSeconds(charInterval);
        }
    }

    void ConnectWebSocket()
    {
        // session_id 讓後端 /session/{id}/control 知道要把治療師的暫停/繼續等
        // 指令轉發到哪一條連線（見 app/ws_registry.py），比照 GameController.ConnectWebSocket。
        string sessionId = PlayerPrefs.GetString("session_id", "");
        string url = string.IsNullOrEmpty(sessionId) ? serverUrl : $"{serverUrl}?session_id={sessionId}";
        ws = new WebSocket(AuthService.AppendToken(url));
        ws.SslConfiguration.EnabledSslProtocols = System.Security.Authentication.SslProtocols.Tls12;
        ws.OnOpen  += (s, e) => Debug.Log("[Share STT WS] 已連線");
        ws.OnError += (s, e) => Debug.LogError($"[Share STT WS] 錯誤: {e.Message}");
        ws.OnClose += (s, e) => Debug.Log("[Share STT WS] 已關閉");
        ws.OnMessage += (s, e) => {
            if (!e.IsText) return;
            lock (queueLock) incomingMessages.Enqueue(e.Data);
        };
        ws.ConnectAsync();
    }

    void ResetInputText()
    {
        if (inputText == null) return;
        inputText.text = placeholderText;
        inputText.color = new Color(0.67f, 0.67f, 0.67f, 1f);
        displayedText = "";
    }

    void OnSubmit()
    {
        if (isRecording) StopRecording();
        if (sttTimeoutCoroutine != null) { StopCoroutine(sttTimeoutCoroutine); sttTimeoutCoroutine = null; }
        StartCoroutine(SubmitClosing());
    }

    IEnumerator SubmitClosing()
    {
        // 心得回答存進 rounds/round_exchanges（第4回合，type='心得'）並觸發療程評估寫入，
        // 跟前三回合的 PostTranscript（純統計用）不同，這裡是心得回合唯一的持久化寫入路徑。
        // 一律呼叫（即使長者沒說話也送空字串）：後端 build_closing_receiving 會把沉默
        // 分類成 thin_or_silent 挑對應的承接語，而且評估寫入本來就不能因為長者沒回應
        // 這題就跳過。
        submitButton.interactable = false;
        if (micButton != null) micButton.interactable = false;

        string sessionId = PlayerPrefs.GetString("session_id", "");
        string closingMessage = "";
        string answerText = string.IsNullOrWhiteSpace(displayedText) ? "" : displayedText;
        if (!string.IsNullOrEmpty(sessionId))
            yield return PostClosingAnswer(sessionId, answerText, msg => closingMessage = msg);

        // 承接語＋收尾肯定語（見 app/services/closing_templates.py）顯示給長者看幾秒，
        // 讓療程有好好被送出去的感覺，再轉場，不是送出後畫面立刻消失。
        if (!string.IsNullOrEmpty(closingMessage) && closingText != null)
        {
            if (replayCoroutine != null) StopCoroutine(replayCoroutine);
            closingText.text = closingMessage;
            yield return new WaitForSeconds(closingMessageDisplaySeconds);
        }

        PlayerPrefs.SetString("NextScene", "ThankYouScene");
        SceneManager.LoadScene("LoadingScene");
    }

    IEnumerator PostClosingAnswer(string sessionId, string text, System.Action<string> onMessage)
    {
        // JsonUtility.ToJson 不支援直接序列化裸字串（只能序列化 [Serializable]
        // 物件），對字串呼叫會回傳 "{}"，導致送出的 JSON 變成 {"text":{}}，
        // 後端 Pydantic 驗證型別不符直接 422。要包成物件再序列化。
        byte[] body = Encoding.UTF8.GetBytes(JsonUtility.ToJson(new TextPayload { text = text }));
        using var req = new UnityWebRequest($"{backendUrl}/session/{sessionId}/closing", "POST");
        req.uploadHandler   = new UploadHandlerRaw(body);
        req.downloadHandler = new DownloadHandlerBuffer();
        req.SetRequestHeader("Content-Type", "application/json");
        AuthService.AttachAuthHeader(req);
        yield return req.SendWebRequest();
        if (req.result != UnityWebRequest.Result.Success)
        {
            Debug.LogWarning($"[Share Closing] POST 失敗: {req.error}");
            yield break;
        }
        ClosingResponse resp;
        try { resp = JsonUtility.FromJson<ClosingResponse>(req.downloadHandler.text); }
        catch { yield break; }
        if (resp != null && !string.IsNullOrEmpty(resp.closing_message))
            onMessage(resp.closing_message);
    }

    void OnMicToggle()
    {
        if (!isRecording) StartRecording();
        else              StopRecording();
    }

    void StartRecording()
    {
        isRecording = true;

        if (typingCoroutine != null) StopCoroutine(typingCoroutine);
        displayedText  = "";
        inputText.text = "錄音中...";
        inputText.color = new Color(1f, 0.4f, 0.4f, 1f);
        if (micButtonImage != null) micButtonImage.color = new Color(1f, 0.3f, 0.3f, 1f);
        RefreshSubmitButton();

        if (UseKinect)
        {
            kinectAudioSender.StartSTT();
        }
        else
        {
            if (Microphone.devices.Length == 0)
            {
                Debug.LogWarning("[Share Mic] 找不到麥克風裝置");
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
    }

    void StopRecording()
    {
        isRecording = false;
        isWaitingForStt = true;
        // 錄音中若已經收到中間辨識結果（逐字動畫已經把長者的原話打上去），
        // 就不要蓋掉；只有完全還沒辨識到任何內容時才顯示「辨識中...」。
        if (string.IsNullOrEmpty(displayedText))
            inputText.text = "辨識中...";
        inputText.color = new Color(0.2f, 0.2f, 0.2f, 1f);
        if (micButtonImage != null) micButtonImage.color = Color.white;
        RefreshSubmitButton();
        if (sttTimeoutCoroutine != null) StopCoroutine(sttTimeoutCoroutine);
        sttTimeoutCoroutine = StartCoroutine(SttTimeout());

        if (UseKinect)
            kinectAudioSender.StopSTT();
        else
        {
            Microphone.End(micDevice);
            SendControl("end");
        }
    }

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

    private void OnKinectSttMessage(string json)
    {
        lock (queueLock) incomingMessages.Enqueue(json);
    }

    void HandleSTTMessage(string json)
    {
        STTMessage msg;
        try { msg = JsonUtility.FromJson<STTMessage>(json); }
        catch { Debug.LogWarning("[Share STT] 無法解析: " + json); return; }

        if (msg == null) return;

        if (msg.type == "control")
        {
            switch (msg.action)
            {
                case "pause":
                    isPaused = true;
                    micButton.interactable = false;
                    submitButton.interactable = false;
                    break;
                case "resume":
                    isPaused = false;
                    micButton.interactable = true;
                    RefreshSubmitButton();
                    break;
                case "replay_audio":
                    OnReplay();
                    break;
                case "end":
                    Application.Quit();
                    break;
            }
            return;
        }

        if (msg.type != "transcript") return;

        // 逐字動畫顯示辨識結果
        if (typingCoroutine != null)
            StopCoroutine(typingCoroutine);
        typingCoroutine = StartCoroutine(TypeCharByChar(msg.text));

        if (msg.isFinal)
        {
            OnSttFinal();
            if (!string.IsNullOrWhiteSpace(msg.text))
                StartCoroutine(PostTranscript(msg.text));
        }
    }

    IEnumerator SttTimeout()
    {
        yield return sttTimeoutWait;
        OnSttFinal();
    }

    void OnSttFinal()
    {
        if (sttTimeoutCoroutine != null) { StopCoroutine(sttTimeoutCoroutine); sttTimeoutCoroutine = null; }
        isWaitingForStt = false;
        RefreshSubmitButton();
    }

    void RefreshSubmitButton()
    {
        submitButton.interactable = !isRecording && !isWaitingForStt && !isPaused;
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

    IEnumerator PostTranscript(string text)
    {
        string sessionId = PlayerPrefs.GetString("session_id", "");
        if (string.IsNullOrEmpty(sessionId)) yield break;
        // JsonUtility.ToJson 不支援直接序列化裸字串（只能序列化 [Serializable]
        // 物件），對字串呼叫會回傳 "{}"，導致送出的 JSON 變成 {"text":{}}，
        // 後端 Pydantic 驗證型別不符直接 422。要包成物件再序列化。
        byte[] body = Encoding.UTF8.GetBytes(JsonUtility.ToJson(new TextPayload { text = text }));
        using var req = new UnityWebRequest($"{backendUrl}/session/{sessionId}/response", "POST");
        req.uploadHandler   = new UploadHandlerRaw(body);
        req.downloadHandler = new DownloadHandlerBuffer();
        req.SetRequestHeader("Content-Type", "application/json");
        AuthService.AttachAuthHeader(req);
        yield return req.SendWebRequest();
        if (req.result != UnityWebRequest.Result.Success)
            Debug.LogWarning($"[Share Transcript] POST 失敗: {req.error}");
    }

    void OnDestroy()
    {
        if (!UseKinect && isRecording)
            Microphone.End(micDevice);
        ws?.Close();
    }
}
