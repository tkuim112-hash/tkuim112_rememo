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
    [Tooltip("拖入場景中的 HandCursorRemapper；治療師端暫停/繼續時用來鎖定/解鎖手部游標")]
    public HandCursorRemapper handCursorRemapper;

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
    private bool isTyping = false;
    private bool hasRecordedOnce = false;
    private Coroutine sttTimeoutCoroutine;
    private readonly WaitForSeconds sttTimeoutWait = new WaitForSeconds(5f);

    // 跟 GameController.cs 同一套設計：STT辨識完成後不直接讓長者送出，先鎖畫面
    // 送治療師平板審核，等治療師確認（可能編輯過）後才顯示給長者看、解鎖送出鍵。
    private bool isPendingTherapistReview = false;
    private ClosingReviewResponse pendingFinalResponse = null;

    [System.Serializable]
    private class ControlPayload { public string type; }

    [System.Serializable]
    private class STTMessage { public string type; public string text; public bool isFinal; public string action; }

    [System.Serializable]
    private class ClosingResponse { public bool ok; public string closing_message; }

    [System.Serializable]
    private class TextPayload { public string text; }

    // /ws/stt 推播的 "final_closing_response" 訊息——治療師確認（可能編輯過）長者
    // 心得回答後的結果，見 HandleSTTMessage／ApplyFinalClosingResponse。
    [System.Serializable]
    private class ClosingReviewResponse { public string type; public string elder_response; public string closing_message; }

    private bool UseKinect => kinectAudioSender != null;

    void Start()
    {
        submitButton.onClick.AddListener(OnSubmit);
        micButton.onClick.AddListener(OnMicToggle);
        replayButton.onClick.AddListener(OnReplay);
        ResetInputText();
        LoadClosingText();
        // 場景檔裡 SubmitButton 的 Interactable 預設是打勾的，RefreshSubmitButton
        // 又只在錄音/暫停/動畫等事件才會被呼叫，不主動呼叫一次的話，長者一進畫面
        // 什麼都還沒說，「送出故事」就已經按得下去。這裡先按 hasRecordedOnce=false
        // 的狀態關掉它，逼長者至少錄過一次音、辨識完成才能送出。
        RefreshSubmitButton();

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
        string sessionId = AuthSession.SessionId ?? "";
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
        // KinectButtonHover 是直接 onClick.Invoke()，不會檢查 interactable，
        // 這裡要自己再擋一次，不能只靠游標被鎖走這個側面效果。
        if (!submitButton.interactable) return;
        if (isRecording) StopRecording();
        if (sttTimeoutCoroutine != null) { StopCoroutine(sttTimeoutCoroutine); sttTimeoutCoroutine = null; }

        if (pendingFinalResponse != null)
        {
            // 治療師已經確認（可能編輯過）心得回答，DB 寫入／評估計算治療師確認
            // 當下就做完了，這裡不用再打 API，直接套用收到的結果。
            var resp = pendingFinalResponse;
            pendingFinalResponse = null;
            StartCoroutine(ApplyFinalClosingResponse(resp));
            return;
        }

        // 沒有待套用的治療師確認結果：走舊路徑，給「長者沉默送出空字串」或
        // RequestReview 重試用盡退回舊流程這兩種邊界情況用。
        StartCoroutine(SubmitClosing());
    }

    IEnumerator ApplyFinalClosingResponse(ClosingReviewResponse resp)
    {
        submitButton.interactable = false;
        if (micButton != null) micButton.interactable = false;

        // 承接語＋收尾肯定語（見 app/services/closing_templates.py）顯示給長者看幾秒，
        // 讓療程有好好被送出去的感覺，再轉場，不是送出後畫面立刻消失——跟
        // SubmitClosing 尾段完全同一套邏輯，只是資料來源是治療師推播的結果。
        if (!string.IsNullOrEmpty(resp.closing_message) && closingText != null)
        {
            if (replayCoroutine != null) StopCoroutine(replayCoroutine);
            closingText.text = resp.closing_message;
            yield return new WaitForSeconds(closingMessageDisplaySeconds);
        }

        PlayerPrefs.SetString("NextScene", "ThankYouScene");
        SceneManager.LoadScene("LoadingScene");
    }

    IEnumerator RequestReview(string text)
    {
        string sessionId = AuthSession.SessionId ?? "";
        if (string.IsNullOrEmpty(sessionId)) { isPendingTherapistReview = false; OnSttFinal(); yield break; }

        byte[] payload = Encoding.UTF8.GetBytes(JsonUtility.ToJson(new TextPayload { text = text }));

        // 跟 GameController.cs 的 RequestReview 同一種重試邏輯：這支打不通的話，
        // 治療師平板永遠不會看到這句心得，長者就會永久卡在「等待治療師確認中」，
        // 重試用盡後直接退回舊流程讓長者自己送出。
        const int maxAttempts = 3;
        bool success = false;
        for (int attempt = 1; attempt <= maxAttempts && !success; attempt++)
        {
            using var req = new UnityWebRequest($"{backendUrl}/session/{sessionId}/closing/review_request", "POST");
            req.uploadHandler   = new UploadHandlerRaw(payload);
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");
            AuthService.AttachAuthHeader(req);
            yield return req.SendWebRequest();

            success = req.result == UnityWebRequest.Result.Success;
            if (!success)
            {
                Debug.LogWarning($"[Share ReviewRequest] 第{attempt}次失敗: {req.error}");
                if (attempt < maxAttempts)
                    yield return new WaitForSeconds(1.5f);
            }
        }

        if (!success)
        {
            Debug.LogError("[Share ReviewRequest] 重試用盡，退回原本流程讓長者直接送出");
            isPendingTherapistReview = false;
            // 退回舊流程時要把畫面從「等待治療師確認中」換回長者原本說的話，
            // 不然畫面卡著審核中的文案、但送出鍵卻已經解鎖，長者會看不懂發生什麼事。
            inputText.text = displayedText;
            inputText.color = new Color(0.2f, 0.2f, 0.2f, 1f);
            if (!isPaused && micButton != null) micButton.interactable = true;
            OnSttFinal();
        }
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

        string sessionId = AuthSession.SessionId ?? "";
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
        if (!micButton.interactable) return;
        if (!isRecording) StartRecording();
        else              StopRecording();
    }

    void StartRecording()
    {
        isRecording = true;

        if (typingCoroutine != null) StopCoroutine(typingCoroutine);
        isTyping = false;
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
                    if (handCursorRemapper != null) handCursorRemapper.SetLocked(true);
                    break;
                case "resume":
                    isPaused = false;
                    // 還在等治療師審核心得回答時麥克風維持鎖定，理由同 GameController.cs
                    // 的 resume 處理。
                    if (!isPendingTherapistReview) micButton.interactable = true;
                    RefreshSubmitButton();
                    if (handCursorRemapper != null) handCursorRemapper.SetLocked(false);
                    break;
                case "replay_audio":
                    // 暫停中不重播，跟 GameController 的 replay_audio 處理一致。
                    if (!isPaused)
                        OnReplay();
                    break;
                case "end":
                    // 治療師手動結束，直接走 ThankYouScene，沿用 LoadingScene 轉場
                    // （ThankYouController 不需要任何 PlayerPrefs 資料）。
                    PlayerPrefs.SetString("NextScene", "ThankYouScene");
                    SceneManager.LoadScene("LoadingScene");
                    break;
            }
            return;
        }

        if (msg.type == "final_closing_response")
        {
            // 治療師在平板確認（可能編輯過）長者的心得回答後，後端推播過來的結果——
            // 資料庫寫入／評估計算治療師確認當下就做完了，這裡只是把確認後的文字
            // 顯示給長者看，等長者按送出時直接套用（見 ApplyFinalClosingResponse／OnSubmit）。
            ClosingReviewResponse resp;
            try { resp = JsonUtility.FromJson<ClosingReviewResponse>(json); }
            catch { return; }
            isPendingTherapistReview = false;
            pendingFinalResponse = resp;
            if (typingCoroutine != null) StopCoroutine(typingCoroutine);
            isTyping = false;
            displayedText = resp.elder_response;
            inputText.text = resp.elder_response;
            inputText.color = new Color(0.2f, 0.2f, 0.2f, 1f);
            hasRecordedOnce = true;
            RefreshSubmitButton();
            return;
        }

        if (msg.type != "transcript") return;

        if (msg.isFinal && !string.IsNullOrWhiteSpace(msg.text))
        {
            // 有辨識到文字：不再用逐字動畫把結果打在長者畫面上（治療師確認前
            // 長者不該看到這句話），改成鎖畫面顯示「等待治療師確認中」，送治療師
            // 平板審核。displayedText 背後還是要記住這句話，PostTranscript／
            // RequestReview 才送得出去。
            if (typingCoroutine != null) StopCoroutine(typingCoroutine);
            isTyping = false;
            displayedText = msg.text;
            isPendingTherapistReview = true;
            inputText.text = "等待治療師確認中";
            inputText.color = new Color(0.2f, 0.2f, 0.2f, 1f);
            micButton.interactable = false;
            RefreshSubmitButton();
            StartCoroutine(PostTranscript(msg.text));   // 統計用途，維持不變
            StartCoroutine(RequestReview(msg.text));
            return;
        }

        // 沒辨識到文字，或還是 interim 結果：維持原本逐字動畫顯示行為
        // （interim 結果本來就會反覆被更新的最終結果覆蓋，不受這次改動影響）。
        if (typingCoroutine != null)
            StopCoroutine(typingCoroutine);
        typingCoroutine = StartCoroutine(TypeCharByChar(msg.text));

        if (msg.isFinal)
        {
            // 沒說話：維持原本允許沉默直接送出空字串的行為，不需要治療師介入。
            OnSttFinal();
        }
    }

    IEnumerator SttTimeout()
    {
        yield return sttTimeoutWait;
        // 逾時仍沒收到真正的 isFinal，直接把 label 換成固定文案（比照
        // GameController.cs 的 OnSttFinal 作法），避免畫面卡在「辨識中...」
        // 但按鈕已經因為 hasRecordedOnce=true 打開的矛盾狀態。停掉還在跑的
        // 逐字動畫，不然它之後恢復執行會把這行文案蓋掉。
        if (typingCoroutine != null) StopCoroutine(typingCoroutine);
        isTyping = false;
        inputText.text = "辨識完成，請按送出";
        OnSttFinal();
    }

    void OnSttFinal()
    {
        if (sttTimeoutCoroutine != null) { StopCoroutine(sttTimeoutCoroutine); sttTimeoutCoroutine = null; }
        isWaitingForStt = false;
        hasRecordedOnce = true;
        RefreshSubmitButton();
    }

    void RefreshSubmitButton()
    {
        // hasRecordedOnce：一定要錄過一次音、辨識完成過才能送出，避免長者一進畫面
        // 什麼都沒說就直接按下「送出故事」。
        // isTyping：isFinal 一到就會 RefreshSubmitButton，但逐字動畫是非同步跑的，
        // 沒有這個條件的話按鈕會在動畫還沒把最終結果完整打出來前就先亮起，
        // 這時候按下送出，SubmitClosing 讀到的 displayedText 只會是動畫跑到一半的
        // 半截文字，不是完整的最終辨識結果。
        // pendingFinalResponse != null：治療師已經確認（可能編輯過）心得回答，長者
        // 只需要看完內容按送出；isPendingTherapistReview 則整個鎖死，避免審核結果
        // 還沒回來時誤觸（理由同 GameController.cs 的 RefreshSubmitButton）。
        submitButton.interactable = !isPaused && !isTyping && !isPendingTherapistReview &&
            (pendingFinalResponse != null ||
             (hasRecordedOnce && !isRecording && !isWaitingForStt));
    }

    // ── 逐字打字動畫（像 Google 語音輸入） ──

    IEnumerator TypeCharByChar(string target)
    {
        isTyping = true;
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

        isTyping = false;
        RefreshSubmitButton();
    }

    IEnumerator PostTranscript(string text)
    {
        string sessionId = AuthSession.SessionId ?? "";
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
