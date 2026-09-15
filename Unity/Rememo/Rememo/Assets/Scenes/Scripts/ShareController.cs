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
    [Tooltip("心得環節開場語音（感謝語／問句，見 LoadClosingText）播放用")]
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
    private Coroutine replayCoroutine;
    private bool isWaitingForStt = false;
    private bool isPaused = false;
    private bool hasRecordedOnce = false;
    private Coroutine sttTimeoutCoroutine;
    private readonly WaitForSeconds sttTimeoutWait = new WaitForSeconds(5f);

    // 跟 GameController.cs 同一套設計：STT辨識完成後不直接讓長者送出，先鎖畫面
    // 送治療師平板審核，等治療師確認（可能編輯過）後才顯示給長者看、解鎖送出鍵。
    // pendingConfirmedText 只存確認後的文字，不像舊版存已經算好的完整結果
    // （2026-09-06 改版，理由跟 GameController.cs 的 pendingConfirmedText
    // 說明一樣：延後評估流程到長者真的按下送出才觸發，避免長者還沒按送出、
    // 療程就被結束時，已經算好的結果被整份丟棄）。
    private bool isPendingTherapistReview = false;
    private string pendingConfirmedText = null;
    // 跟 GameController.cs 同一套保底邏輯：/ws/stt 在治療師審核心得回答那段
    // 容易閒置逾時斷線、"confirmed_closing_text" 推播漏接就沒有補送機制，
    // 這裡定時輪詢 /metrics 把確認結果追回來（見 PollForConfirmedText）。
    private Coroutine reviewPollCoroutine;

    [System.Serializable]
    private class ControlPayload { public string type; }

    [System.Serializable]
    private class STTMessage { public string type; public string text; public bool isFinal; public string action; public string elder_response; }

    [System.Serializable]
    private class MetricsResponse { public string review_status; public string elder_response; }

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
        string thanks = PlayerPrefs.GetString("ClosingThanks", "");
        string question = PlayerPrefs.GetString("ClosingQuestion", "");
        // 兩段（感謝語／問題）各自可能是空字串，只把有內容的段落接起來，
        // 避免空字串還是接了一個換行、畫面上多出空行。
        // 2026-09-08：原本這裡還有第三段「承接語」，三段拼起來常常超過
        // AIBubble 背景圖（st.png）畫死的4條格線，超出的行沒有格線可以
        // 對齊、畫面跑版，使用者決定拿掉承接語，只留感謝語＋問題。
        var segments = new List<string>();
        if (!string.IsNullOrEmpty(thanks)) segments.Add(thanks);
        if (!string.IsNullOrEmpty(question)) segments.Add(question);
        closingFullText = string.Join("\n", segments);
        closingText.text = closingFullText;

        // 感謝語／問句兩段都是前端內建預錄音檔（見 audio_bank.py，
        // GameController.SendResponse 存進 PlayerPrefs 時已經用 '|' 串好），
        // 依序接起來播——這裡沒有需要即時TTS的動態內容，全部是本地 key，
        // 不用像 GameController 那樣還要組後端下載的 audio_path。
        closingAudioUris = new List<string>();
        closingAudioUris.AddRange(LocalAudioPlayer.BuildUris(null, SplitAudioKeys("ClosingThanksAudioKeys")));
        closingAudioUris.AddRange(LocalAudioPlayer.BuildUris(null, SplitAudioKeys("ClosingQuestionAudioKeys")));
        // 心得環節的內容剛顯示，同 GameController.ApplyRoundResponse：先標記
        // 還沒進入回答等待期，語音播完/估讀時間到（OnQuestionAsked）才算開始。
        kinectSensorSender?.OnNewQuestionDisplayed();
        // 反應時間計時要等長者真的看完/聽完這幾段內容才開始，理由同
        // GameController.ApplyRoundResponse——播放中的那幾秒不該算進反應時間；
        // 萬一這幾段剛好都沒有對應音檔（closingAudioUris 是空的），改用估算
        // 的閱讀時間頂替，不要直接立刻開始計時。
        if (closingAudioUris.Count > 0)
            StartCoroutine(LocalAudioPlayer.PlaySequence(audioSource, closingAudioUris, () => kinectSensorSender?.OnQuestionAsked()));
        else
            StartCoroutine(LocalAudioPlayer.DelayedAction(
                LocalAudioPlayer.EstimateReadingSeconds(closingFullText),
                () => kinectSensorSender?.OnQuestionAsked()));
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

        // pendingConfirmedText != null（治療師已確認，可能編輯過）時，
        // displayedText 在收到 confirmed_closing_text 訊息當下已經設成確認
        // 後的文字（見 HandleSTTMessage）；不管是不是走治療師審核流程，
        // SubmitClosing 都是讀 displayedText 送出，兩條路徑共用同一支，
        // DB 寫入／評估計算延後到這裡才真正觸發（2026-09-06 改版，見
        // pendingConfirmedText 欄位說明，取代原本的 ApplyFinalClosingResponse）。
        pendingConfirmedText = null;
        StartCoroutine(SubmitClosing());
    }

    IEnumerator RequestReview(string text)
    {
        string sessionId = AuthSession.SessionId ?? "";
        if (string.IsNullOrEmpty(sessionId)) { isPendingTherapistReview = false; hasRecordedOnce = true; OnSttFinal(); yield break; }

        byte[] payload = Encoding.UTF8.GetBytes(JsonUtility.ToJson(new TextPayload { text = text }));

        // 跟 GameController.cs 的 RequestReview 同一種重試邏輯：這支打不通的話，
        // 治療師平板永遠不會看到這句心得，長者就會永久卡在「等待輔導員確認中」，
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
            // 退回舊流程時要把畫面從「等待輔導員確認中」換回長者原本說的話，
            // 不然畫面卡著審核中的文案、但送出鍵卻已經解鎖，長者會看不懂發生什麼事。
            inputText.text = displayedText;
            inputText.color = new Color(0.2f, 0.2f, 0.2f, 1f);
            if (!isPaused && micButton != null) micButton.interactable = true;
            hasRecordedOnce = true;
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
        displayedText  = "";
        inputText.text = "錄音中...";
        inputText.color = new Color(1f, 0.4f, 0.4f, 1f);
        if (micButtonImage != null) micButtonImage.color = new Color(1f, 0.3f, 0.3f, 1f);
        RefreshSubmitButton();

        kinectSensorSender?.OnMicPressed();

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

        if (msg.type == "confirmed_closing_text")
        {
            // 治療師在平板確認（可能編輯過）長者的心得回答後，後端推播過來的確認
            // 文字——2026-09-06 改版後，資料庫寫入／評估計算延後到長者真的按下
            // 送出才觸發（見 OnSubmit／SubmitClosing，取代原本的
            // ApplyFinalClosingResponse），這裡只是把確認後的文字顯示給長者看。
            //
            // 治療師在長者按送出前可以重新編輯、再確認一次（pending_closing_review
            // 支援重複覆蓋，見 session.py session_closing_confirm_response），所以
            // 不能只在 isPendingTherapistReview 還是 true 時才套用——無條件套用，
            // 跟 PollForConfirmedText 共用同一份文字比對邏輯（見該函式）。
            ApplyConfirmedText(msg.elder_response);
            return;
        }

        if (msg.type != "transcript") return;
        // 長者不會在畫面上看到辨識出的文字，只在背後記錄下來供送出時使用；
        // inputText 維持 StartRecording/StopRecording 設的「錄音中...」「辨識中...」狀態，
        // 直到 OnSttFinal 換成「辨識完成，請按送出」（跟 GameController.cs 行為一致）。
        bool hasText = !string.IsNullOrWhiteSpace(msg.text);
        if (hasText) displayedText = msg.text;
        if (msg.isFinal)
        {
            if (hasText)
            {
                // 同 GameController.cs HandleSTTMessage：StopRecording 啟動的
                // sttTimeoutCoroutine（5秒後跳「辨識完成，請按送出」）是給沒進
                // 審核流程的舊路徑用的，進審核流程一定要取消，否則就算治療師
                // 很快確認完、畫面已經套用正確文字，5秒一到還是會被 OnSttFinal
                // 蓋回「辨識完成，請按送出」，治療師常誤以為確認沒生效又點一次
                // （2026-09-07 稽核：心得回合反映的「按兩次才成功」同一根因）。
                if (sttTimeoutCoroutine != null) { StopCoroutine(sttTimeoutCoroutine); sttTimeoutCoroutine = null; }
                isPendingTherapistReview = true;
                inputText.text = "等待輔導員確認中";
                inputText.color = new Color(0.2f, 0.2f, 0.2f, 1f);
                micButton.interactable = false;
                RefreshSubmitButton();
                StartCoroutine(PostTranscript(msg.text));   // 統計用途，維持不變
                StartCoroutine(RequestReview(msg.text));
                if (reviewPollCoroutine != null) StopCoroutine(reviewPollCoroutine);
                reviewPollCoroutine = StartCoroutine(PollForConfirmedText());
            }
            else
            {
                // 沒說話（或 Kinect 收音太小聲被 VAD 判定成整段靜音）：提示長者再試
                // 一次，並把 hasRecordedOnce 收回去鎖住送出鍵（見 RefreshSubmitButton），
                // 強制長者重新錄音才能送出，不再允許直接送出空字串。
                hasRecordedOnce = false;
                OnSttFinal("請大聲一點再試一次");
            }
        }
    }

    // 治療師確認（可能編輯過）心得回答後，套用到長者畫面——不管是從
    // /ws/stt 收到 "confirmed_closing_text" 推播（正常路徑），還是
    // PollForConfirmedText 輪詢追回來的（WS 推播漏接時的保底），都走這支，
    // 兩邊行為才不會分岔（比照 GameController.cs 的 ApplyConfirmedText）。
    void ApplyConfirmedText(string text)
    {
        isPendingTherapistReview = false;
        pendingConfirmedText = text;
        displayedText = text;
        inputText.text = text;
        inputText.color = new Color(0.2f, 0.2f, 0.2f, 1f);
        hasRecordedOnce = true;
        RefreshSubmitButton();
    }

    // 比照 GameController.cs 的 PollForConfirmedText：定時輪詢 /metrics
    // （跟治療師網頁 polling 同一支 API）當保底，review_status 變成
    // awaiting_closing_submit 就代表治療師已經確認過、只是 WS 沒送到。
    // 存活範圍蓋 isPendingTherapistReview（等第一次確認）跟 pendingConfirmedText
    // != null（已經確認過、長者還沒按送出，治療師隨時可能重新編輯再送一次）
    // 兩個階段，理由跟 GameController.cs 的 PollForConfirmedText 一樣：重新編輯
    // 那次推播一樣可能撞上斷線空窗，只保第一次確認的話，治療師改第二次時反而
    // 沒有保底。用文字比對（跟目前畫面上的 pendingConfirmedText 不同才套用）
    // 避免把治療師剛編輯的新版本蓋回舊版本。
    IEnumerator PollForConfirmedText()
    {
        string sessionId = AuthSession.SessionId ?? "";
        if (string.IsNullOrEmpty(sessionId)) yield break;
        var wait = new WaitForSeconds(3f);
        while (isPendingTherapistReview || pendingConfirmedText != null)
        {
            yield return wait;
            if (!(isPendingTherapistReview || pendingConfirmedText != null)) yield break;

            using var req = UnityWebRequest.Get($"{backendUrl}/session/{sessionId}/metrics");
            AuthService.AttachAuthHeader(req);
            yield return req.SendWebRequest();
            if (req.result != UnityWebRequest.Result.Success) continue;
            if (!(isPendingTherapistReview || pendingConfirmedText != null)) yield break;   // 長者這時候已經按送出了

            MetricsResponse resp;
            try { resp = JsonUtility.FromJson<MetricsResponse>(req.downloadHandler.text); }
            catch { continue; }
            bool hasConfirmed = resp != null && resp.review_status == "awaiting_closing_submit"
                && !string.IsNullOrEmpty(resp.elder_response);
            if (hasConfirmed && resp.elder_response != pendingConfirmedText)
                ApplyConfirmedText(resp.elder_response);
        }
    }

    IEnumerator SttTimeout()
    {
        yield return sttTimeoutWait;
        hasRecordedOnce = true;
        OnSttFinal();
    }

    void OnSttFinal(string message = "辨識完成，請按送出")
    {
        if (sttTimeoutCoroutine != null) { StopCoroutine(sttTimeoutCoroutine); sttTimeoutCoroutine = null; }
        isWaitingForStt = false;
        inputText.text = message;
        RefreshSubmitButton();
    }

    void RefreshSubmitButton()
    {
        // hasRecordedOnce：一定要錄過一次音、辨識完成過才能送出，避免長者一進畫面
        // 什麼都沒說就直接按下「送出故事」。
        // pendingConfirmedText != null：治療師已經確認（可能編輯過）心得回答，長者
        // 只需要看完內容按送出；isPendingTherapistReview 則整個鎖死，避免審核結果
        // 還沒回來時誤觸（理由同 GameController.cs 的 RefreshSubmitButton）。
        submitButton.interactable = !isPaused && !isPendingTherapistReview &&
            (pendingConfirmedText != null ||
             (hasRecordedOnce && !isRecording && !isWaitingForStt));
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
