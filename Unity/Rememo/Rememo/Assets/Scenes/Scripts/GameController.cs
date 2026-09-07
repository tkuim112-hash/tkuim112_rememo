using UnityEngine;
using UnityEngine.UI;
using UnityEngine.SceneManagement;
using UnityEngine.Networking;
using TMPro;
using System.Collections;
using System.Collections.Generic;
using System.Text;
using WebSocketSharp;

public class GameController : MonoBehaviour
{
    [Header("回合設定")]
    public int totalRounds = 3;
    public static int currentRound = 1;

    [Header("後端設定")]
    public string backendUrl = "https://api.re-memo.com";
    public string userId = "user_001";
    public string sessionId = "sess_001";

    [Header("UI 元件")]
    public TMP_Text roundNumber;
    public TMP_Text roundTitle;
    public TMP_Text roundSub;
    public Button submitButton;
    public Button micButton;
    public Image micButtonImage;
    public Button replayButton;
    public AudioSource audioSource;
    public TMP_Text aiText;
    public TMP_Text inputText;
    public GameObject loadingSpinner;
    public RawImage photoDisplay;
    public GameObject generatingImageText;

    [Header("Kinect 整合")]
    [Tooltip("拖入場景中的 KinectAudioSender；若留空則退回使用內建麥克風")]
    public KinectAudioSender kinectAudioSender;
    [Tooltip("拖入場景中的 KinectSensorSender；若留空則不追蹤反應時間")]
    public KinectSensorSender kinectSensorSender;
    [Tooltip("拖入場景中的 HandCursorRemapper；治療師端暫停/繼續時用來鎖定/解鎖手部游標")]
    public HandCursorRemapper handCursorRemapper;

    [Header("WebSocket STT 設定（內建麥克風模式用）")]
    public string sttServerUrl = "wss://api.re-memo.com/ws/stt";
    public int sampleRate = 16000;
    public int maxRecordSeconds = 60;

    private string[] roundNames = { "第一回合", "第二回合", "第三回合" };
    private string placeholderText = "想到什麼就說什麼，按下麥克風可以用說的…";

    private SessionStateData currentState;

    // STT
    private WebSocket ws;
    private AudioClip micClip;
    private string micDevice;
    private int lastSamplePos = 0;
    private bool isRecording = false;
    private bool isWaitingForStt = false;
    private bool isSubmitting = false;
    private bool isPaused = false;
    private bool hasSpeechInput = false;
    // STT辨識完成後不再直接讓長者送出，改成鎖畫面等治療師在平板審核/編輯——
    // isPendingTherapistReview 鎖住麥克風/送出鍵；pendingConfirmedText 存治療師
    // 確認後（透過 /ws/stt 推播的 "confirmed_text" 訊息）收到的文字，長者按下
    // 送出時才拿這段文字去呼叫 /session/respond 真正觸發生成（2026-09-06 改版：
    // 原本治療師一確認就先呼叫完 LLM/RAG/生圖、把完整結果存進 pendingFinalResponse
    // 讓長者按送出時直接套用、感覺是瞬間完成，但代價是長者還沒按送出、療程就被
    // 結束時，這些已經算好的結果（含真的花 API 成本生成的圖片）會整份被丟棄，
    // 稽核當天測試就踩到兩次白工。改成生成延後到長者真的按下送出才觸發，確保
    // 生成一定會被用到，代價是長者按送出後要重新等一次生成時間）。
    private bool isPendingTherapistReview = false;
    private string pendingConfirmedText = null;
    // /ws/stt 連線在回合間（治療師審核那段）容易被判定逾時斷線、推播漏接
    // （見 app/routers/session.py session_confirm_response 的說明）——這條
    // coroutine 是保底：isPendingTherapistReview 期間定時輪詢 /metrics，
    // WS 沒送達的話，靠這裡把確認後文字追回來，不用一直卡在「等待輔導員
    // 確認中」。
    private Coroutine reviewPollCoroutine;
    private Coroutine sttTimeoutCoroutine;
    private readonly WaitForSeconds sttTimeoutWait = new WaitForSeconds(5f);
    private const string NoResponseMarker = "（長者未回應）";
    private readonly Queue<string> incomingMessages = new Queue<string>();
    private readonly object queueLock = new object();
    private string displayedText = "";

    // 內建麥克風模式的 STT WebSocket 斷線重連（見 ConnectWebSocket 內的 OnClose/OnError）。
    // 治療師的暫停/跳過等指令要靠這條連線才送得到，斷線後若不重連，按鈕會永久失效
    // 直到長者重開 App。
    private volatile bool needsWsReconnect = false;
    private float wsReconnectTimer = 0f;
    private bool isQuitting = false;
    private const float WsReconnectDelay = 3f;

    private bool UseKinect => kinectAudioSender != null;

    [System.Serializable]
    private class ControlPayload { public string type; }

    [System.Serializable]
    private class TextPayload { public string text; }

    [System.Serializable]
    private class STTMessage { public string type; public string text; public bool isFinal; public string action; public string elder_response; }

    [System.Serializable]
    private class MetricsResponse { public string review_status; public string elder_response; }

    void Start()
    {
        string selectedPatientId = PlayerPrefs.GetString("SelectedPatientId", "");
        if (!string.IsNullOrEmpty(selectedPatientId)) userId = selectedPatientId;

        // 跟 WarmupScene 的 KinectCalibrationManager 用同一組 session_id（由 SessionService
        // 在 UserSelectScene 選定病患時換好），校正資料才會跟這次療程的對話記錄綁在一起。
        string sharedSessionId = AuthSession.SessionId ?? "";
        if (!string.IsNullOrEmpty(sharedSessionId)) sessionId = sharedSessionId;

        submitButton.onClick.AddListener(OnSubmit);
        if (micButton != null) micButton.onClick.AddListener(OnMicToggle);
        if (replayButton != null) replayButton.onClick.AddListener(OnReplayAudio);
        ResetInputText();
        RefreshSubmitButton();
        UpdateRoundBadge();
        loadingSpinner.SetActive(false);
        if (generatingImageText != null) generatingImageText.SetActive(false);

        if (UseKinect)
            // 掛在 Start() 而不是 StartRecording()：治療師端的暫停/繼續/跳過/重播指令
            // 隨時可能在長者第一次按麥克風之前就送到，這裡要先掛好才不會漏接。
            kinectAudioSender.OnSttMessage = OnKinectSttMessage;
        else
            ConnectWebSocket();

        if (currentRound == 1 && PendingSessionStart.Response != null)
        {
            var resp = PendingSessionStart.Response;
            PendingSessionStart.Response = null;
            ApplyRoundResponse(resp);
        }
        else
        {
            StartCoroutine(StartRound(currentRound));
        }
    }

    // ─── STT ──────────────────────────────────────────────────────

    void ConnectWebSocket()
    {
        // session_id 讓後端 /session/{id}/control 知道要把治療師的重播/跳過/暫停/繼續
        // 指令轉發到哪一條連線（見 app/ws_registry.py）。
        string url = string.IsNullOrEmpty(sessionId) ? sttServerUrl : $"{sttServerUrl}?session_id={sessionId}";
        ws = new WebSocket(AuthService.AppendToken(url));
        ws.SslConfiguration.EnabledSslProtocols = System.Security.Authentication.SslProtocols.Tls12;
        ws.OnOpen  += (s, e) => Debug.Log("[Game STT WS] 已連線");
        ws.OnError += (s, e) => { Debug.LogError($"[Game STT WS] 錯誤: {e.Message}"); needsWsReconnect = true; };
        ws.OnClose += (s, e) => { Debug.Log("[Game STT WS] 已關閉"); if (!isQuitting) needsWsReconnect = true; };
        ws.OnMessage += (s, e) => {
            if (!e.IsText) return;
            lock (queueLock) incomingMessages.Enqueue(e.Data);
        };
        ws.ConnectAsync();
    }

    void OnMicToggle()
    {
        // KinectButtonHover 是直接 onClick.Invoke()，不會檢查 interactable，
        // 暫停時 micButton.interactable 被設 false 這裡要自己再擋一次，
        // 不能只靠游標被鎖走這個側面效果。
        if (!micButton.interactable) return;
        if (!isRecording) StartRecording();
        else              StopRecording();
    }

    void StartRecording()
    {
        isRecording = true;
        hasSpeechInput = true;
        displayedText = "";
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
            if (Microphone.devices.Length == 0) { isRecording = false; RefreshSubmitButton(); return; }
            if (ws == null || ws.ReadyState != WebSocketState.Open) ConnectWebSocket();
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
        inputText.text  = "辨識中...";
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

    IEnumerator SttTimeout()
    {
        yield return sttTimeoutWait;
        OnSttFinal();
    }

    void OnSttFinal()
    {
        if (sttTimeoutCoroutine != null) { StopCoroutine(sttTimeoutCoroutine); sttTimeoutCoroutine = null; }
        isWaitingForStt = false;
        inputText.text = "辨識完成，請按送出";
        RefreshSubmitButton();
    }

    void RefreshSubmitButton()
    {
        // pendingConfirmedText != null：治療師已經確認（可能編輯過）這一題的回答，
        // 長者只需要看完內容按送出，不受 hasSpeechInput/isRecording/isWaitingForStt
        // 這些「還沒送審」狀態限制；isPendingTherapistReview 則整個鎖死，避免
        // 審核結果還沒回來時誤觸。
        bool enabled = !isSubmitting && !isPaused && !isPendingTherapistReview &&
                       (pendingConfirmedText != null ||
                        (hasSpeechInput && !isRecording && !isWaitingForStt));
        submitButton.interactable = enabled;
        if (submitButton.image != null)
            submitButton.image.color = enabled ? Color.white : new Color(0.55f, 0.55f, 0.55f, 1f);
    }

    void SendControl(string type)
    {
        if (ws?.ReadyState == WebSocketState.Open)
            ws.SendAsync(JsonUtility.ToJson(new ControlPayload { type = type }), null);
    }

    void Update()
    {
        if (!UseKinect && isRecording) StreamMicAudio();
        DrainIncomingMessages();
        if (!UseKinect) { TickWsReconnect(); TickKeepAlive(); }
    }

    void TickWsReconnect()
    {
        if (!needsWsReconnect) return;
        wsReconnectTimer += Time.deltaTime;
        if (wsReconnectTimer < WsReconnectDelay) return;
        wsReconnectTimer = 0f;
        needsWsReconnect = false;
        Debug.Log("[Game STT WS] 嘗試重新連線");
        ConnectWebSocket();
    }

    // 回合間（尤其是答完生圖前引導問題、後端在跑 LLM/生圖那段）長者不會碰麥克風，
    // 這條 STT 連線會閒置一段不確定的時間——太久沒有任何訊框，中間的伺服器/代理
    // 容易把連線判定逾時關掉，長者回來按第一次麥克風時才發現連線已經斷了，
    // 中間重連的那幾秒錄音會漏掉（見 StreamMicAudio 的說明）。這裡固定週期送一個
    // 後端會忽略的輕量心跳文字訊框，讓連線一直有動靜，從源頭避免被判定逾時，
    // 不用等斷線後才補救。
    private float sttKeepAliveTimer = 0f;
    private const float SttKeepAliveInterval = 20f;

    void TickKeepAlive()
    {
        if (ws == null || ws.ReadyState != WebSocketState.Open) { sttKeepAliveTimer = 0f; return; }
        sttKeepAliveTimer += Time.deltaTime;
        if (sttKeepAliveTimer < SttKeepAliveInterval) return;
        sttKeepAliveTimer = 0f;
        SendControl("ping");
    }

    void StreamMicAudio()
    {
        int pos = Microphone.GetPosition(micDevice);
        if (pos < lastSamplePos) lastSamplePos = 0;
        int count = pos - lastSamplePos;
        if (count <= 0) return;

        // 連線還沒開（例如上一回合等治療師審核太久，連線被閒置逾時斷開，
        // StartRecording 觸發的 ConnectWebSocket 還在交握中）就先不要消耗這段
        // 樣本——舊版不管有沒有真的送出都會推進 lastSamplePos，等於把長者
        // 剛開口那幾秒錄音直接丟掉，連線恢復後也補不回來，導致 STT 辨識不到
        // 內容、要按第二次麥克風才會成功（2026-09-07 稽核：回合2開場前那段
        // 等治療師審核的空檔最容易踩到）。這裡改成連線沒開就整段跳過、
        // 下一幀再重新累積，等連線真的開了再一次把累積的樣本送出，不會漏音。
        if (ws == null || ws.ReadyState != WebSocketState.Open) return;

        float[] samples = new float[count];
        micClip.GetData(samples, lastSamplePos);
        lastSamplePos = pos;
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
        catch { return; }
        if (msg == null) return;

        if (msg.type == "control")
        {
            switch (msg.action)
            {
                case "replay_audio":
                    // 暫停中不重播：重播會重新排反應逾時倒數，等於讓暫停中的療程自己繼續跑。
                    if (!isPaused)
                        OnReplayAudio();
                    break;
                case "skip_scene":
                    // 跳過「目前這一題」，不是跳過整個回合：視同長者未回應直接進下一步，
                    // 避免跟錄音/送出中互撞。isPendingTherapistReview 中也不能跳過——
                    // 這句話已經送治療師審核了，治療師平板那邊可能正在編輯，
                    // 這裡搶著用 NoResponseMarker 蓋過去會跟治療師的確認結果打架。
                    if (!isRecording && !isWaitingForStt && !isSubmitting && !isPaused && !isPendingTherapistReview)
                        StartCoroutine(AutoSubmitNoResponse());
                    break;
                case "pause":
                    isPaused = true;
                    micButton.interactable = false;
                    submitButton.interactable = false;
                    if (replayButton != null) replayButton.interactable = false;
                    if (handCursorRemapper != null) handCursorRemapper.SetLocked(true);
                    break;
                case "resume":
                    isPaused = false;
                    RefreshSubmitButton();
                    // 還在等治療師審核時麥克風維持鎖定（理由同 HandleSTTMessage 的
                    // final_response 分支），不能因為「繼續」就解鎖去搶新錄音。
                    if (!isPendingTherapistReview) micButton.interactable = true;
                    if (replayButton != null) replayButton.interactable = true;
                    if (handCursorRemapper != null) handCursorRemapper.SetLocked(false);
                    break;
                case "end":
                    // 治療師手動結束，跳過剩餘回合／心得環節，直接走 ThankYouScene，
                    // 沿用 LoadingScene 轉場（ThankYouController 不需要任何 PlayerPrefs 資料）。
                    PlayerPrefs.SetString("NextScene", "ThankYouScene");
                    SceneManager.LoadScene("LoadingScene");
                    break;
                case "generating_image":
                    // 後端 orchestrator._start_scene_after_detail 真正開始生圖前推播
                    // 的通知（見 app/routers/session.py 呼叫 process_response 時的
                    // on_generating_image callback）；只有這個訊號會打開這個標籤，
                    // 生圖前追問Q2那種不生圖的分支不會走到這裡，不會誤顯示。
                    //
                    // 這個訊號常常是在治療師審核流程中收到的：長者已經回答完目前這題、
                    // 正在等治療師平板確認（isPendingTherapistReview），這時候 aiText
                    // 還顯示著長者剛剛回答過的舊題目。治療師一確認，後端就開始生圖並
                    // 推播這個訊號，畫面會變成「舊題目」+「生圖中」同時出現，看起來
                    // 像是那個舊題目還沒問完、卻已經在生圖，容易誤會（2026-09-06
                    // 稽核：治療師反映生圖時旁邊的題目應該要清掉）。這裡把 aiText 一併
                    // 藏起來，等 ApplyFinalResponse 套用新一輪回應時會自己重新顯示
                    // （見該函式），不用另外處理復原。
                    aiText.gameObject.SetActive(false);
                    if (generatingImageText != null) generatingImageText.SetActive(true);
                    break;
            }
            return;
        }

        if (msg.type == "confirmed_text")
        {
            // 治療師在平板確認（可能編輯過）長者這一題的回答後，後端推播過來的
            // 確認結果——2026-09-06 改版後這裡只有確認後的文字，LLM 分類／RAG／
            // 生圖那些耗時流程延後到長者真的按下送出才觸發（見 OnSubmit／
            // SendResponse，取代原本的 ApplyConfirmedResponse），避免長者還沒
            // 按送出、療程就被結束時，已經算好的結果（含生成的圖片）被整份丟棄。
            //
            // 治療師在長者按送出前可以重新編輯、再確認一次（pending_review 支援
            // 重複覆蓋，見 session.py session_confirm_response），所以這裡不能只在
            // isPendingTherapistReview 還是 true 時才套用——那樣會擋掉「已經確認
            // 過一次、治療師又改了一次」的後續推播。無條件套用，跟 PollForConfirmedText
            // 共用同一份文字比對邏輯（見該函式），避免舊文字蓋掉新文字。
            ApplyConfirmedText(msg.elder_response);
            return;
        }

        if (msg.type != "transcript") return;
        // 長者不會在畫面上看到辨識出的文字，只在背後記錄下來供送出時使用；
        // inputText 維持 StartRecording/StopRecording 設的「錄音中...」「辨識中...」狀態，
        // 直到 OnSttFinal 換成「辨識完成，請按送出」。
        bool hasText = !string.IsNullOrWhiteSpace(msg.text);
        if (hasText)
            displayedText = msg.text;
        if (msg.isFinal)
        {
            if (hasText)
            {
                // 有辨識到文字：不再直接開放長者送出，改成鎖畫面送治療師平板審核。
                // StopRecording 啟動的 sttTimeoutCoroutine（5秒後跳「辨識完成，請按
                // 送出」）是給「沒進審核流程」的舊路徑用的，這裡一定要順手取消——
                // 沒取消的話，就算治療師在5秒內就確認完、畫面已經正確顯示確認後的
                // 文字，5秒一到 OnSttFinal 還是會準時觸發，把畫面蓋回「辨識完成，
                // 請按送出」，看起來像剛剛的確認整個沒生效，治療師常常因此又點一次
                // 確認（2026-09-07 稽核：治療師反映有時候要按兩次確認才成功，但
                // 後端/WS都沒有任何錯誤或漏接紀錄，追下來是這裡的計時器沒取消）。
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
                // 沒辨識到任何文字（例如長者沒說話）：維持原本可直接送出空字串的行為，
                // 不需要治療師介入。
                OnSttFinal();
            }
        }
    }

    // 治療師確認（可能編輯過）這一題的回答後，套用到長者畫面——不管是從
    // /ws/stt 收到 "confirmed_text" 推播（正常路徑），還是 PollForConfirmedText
    // 輪詢追回來的（WS 推播漏接時的保底），都走這支，兩邊行為才不會分岔。
    void ApplyConfirmedText(string text)
    {
        isPendingTherapistReview = false;
        pendingConfirmedText = text;
        displayedText = text;
        inputText.text = text;
        inputText.color = new Color(0.2f, 0.2f, 0.2f, 1f);
        // 麥克風維持鎖定，避免長者在按送出前又開始新錄音——這會讓
        // pendingConfirmedText 跟一段還在錄的新音訊互相打架。等
        // SendResponse 進到下一題/下一回合時才解鎖（見該函式呼叫的
        // ApplyFinalResponse）。
        RefreshSubmitButton();
    }

    // /ws/stt 連線在治療師審核這段常常閒置、容易被判定逾時斷線（見
    // KinectAudioSender.cs TickKeepAlive 說明），"confirmed_text" 這種推播
    // 如果剛好撞上斷線空窗就直接漏接、沒有補送機制，長者會卡在「等待輔導員
    // 確認中」直到治療師發現、自己重按一次。這裡定時改用 HTTP 輪詢 /metrics
    // （跟治療師網頁 polling 同一支 API，見 session.py session_metrics）當
    // 保底，review_status 變成 awaiting_round_submit 就代表治療師已經確認
    // 過、只是 WS 沒送到，直接把 elder_response 追回來。
    //
    // 存活範圍蓋 isPendingTherapistReview（等第一次確認）跟 pendingConfirmedText
    // != null（已經確認過、長者還沒按送出，治療師隨時可能重新編輯再送一次）
    // 兩個階段——重新編輯那次推播一樣可能撞上斷線空窗，只保第一次確認的話，
    // 治療師改第二次時反而沒有保底。用文字比對（跟目前畫面上的
    // pendingConfirmedText 不同才套用）避免把治療師剛編輯的新版本蓋回舊版本。
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
            bool hasConfirmed = resp != null && resp.review_status == "awaiting_round_submit"
                && !string.IsNullOrEmpty(resp.elder_response);
            if (hasConfirmed && resp.elder_response != pendingConfirmedText)
                ApplyConfirmedText(resp.elder_response);
        }
    }

    IEnumerator RequestReview(string text)
    {
        var body = new RespondRequest { elder_response = text, state = currentState };
        byte[] payload = Encoding.UTF8.GetBytes(JsonUtility.ToJson(body));

        // 跟 SendResponse 同一種重試邏輯（3次、間隔1.5秒）：這支打不通的話，
        // 治療師平板永遠不會看到這句話，長者就會永久卡在「等待輔導員確認中」，
        // 比原本 SendResponse 失敗只是「這句話送不出去」更嚴重，所以重試用盡後
        // 直接退回舊流程讓長者自己送出，不留長者卡死的畫面。
        const int maxAttempts = 3;
        bool success = false;
        for (int attempt = 1; attempt <= maxAttempts && !success; attempt++)
        {
            using var req = new UnityWebRequest($"{backendUrl}/session/{sessionId}/review_request", "POST");
            req.uploadHandler   = new UploadHandlerRaw(payload);
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");
            AuthService.AttachAuthHeader(req);
            yield return req.SendWebRequest();

            success = req.result == UnityWebRequest.Result.Success;
            if (!success)
            {
                Debug.LogWarning($"[ReviewRequest] 第{attempt}次失敗: {req.error}");
                if (attempt < maxAttempts)
                    yield return new WaitForSeconds(1.5f);
            }
        }

        if (!success)
        {
            Debug.LogError("[ReviewRequest] 重試用盡，退回原本流程讓長者直接送出");
            isPendingTherapistReview = false;
            if (!isPaused) micButton.interactable = true;
            OnSttFinal();
        }
    }

    IEnumerator PostTranscript(string text)
    {
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
            Debug.LogWarning($"[Transcript] POST 失敗: {req.error}");
    }

    void ResetInputText()
    {
        if (inputText == null) return;
        inputText.text = placeholderText;
        inputText.color = new Color(0.67f, 0.67f, 0.67f, 1f);
        displayedText = "";
        hasSpeechInput = false;
    }

    // ─── 回合流程 ──────────────────────────────────────────────────

    IEnumerator StartRound(int roundNum)
    {
        aiText.gameObject.SetActive(false);
        loadingSpinner.SetActive(true);

        string url = roundNum == 1
            ? $"{backendUrl}/session/start?user_id={userId}&session_id={sessionId}"
            : $"{backendUrl}/session/round?user_id={userId}&session_id={sessionId}&round_number={roundNum}";

        using var req = UnityWebRequest.PostWwwForm(url, "");
        AuthService.AttachAuthHeader(req);
        yield return req.SendWebRequest();

        if (req.result != UnityWebRequest.Result.Success)
        {
            Debug.LogError($"[Session] API 失敗: {req.error}");
            loadingSpinner.SetActive(false);
            yield break;
        }

        var resp = JsonUtility.FromJson<StartRoundResponse>(req.downloadHandler.text);
        loadingSpinner.SetActive(false);
        ApplyRoundResponse(resp);
    }

    // scene_text（開場白／承接語）跟 question 接成一段一起顯示，跟
    // ShareController.cs LoadClosingText 同一套做法——scene_text 之前只被
    // 打包進音檔序列播放語音，畫面上完全沒有顯示，長者只看得到問題本身，
    // 沒有前面的暖場鋪墊（2026-08-19 稽核發現）。
    static string BuildAiText(string sceneText, string question)
    {
        if (string.IsNullOrEmpty(sceneText)) return question;
        if (string.IsNullOrEmpty(question)) return sceneText;
        return $"{sceneText}\n{question}";
    }

    void ApplyRoundResponse(StartRoundResponse resp)
    {
        currentState = resp.state;
        aiText.text = BuildAiText(resp.scene_text, resp.question);
        aiText.gameObject.SetActive(true);
        // 新問題剛顯示，語音/估讀秒數還沒開始算，長者本來就不該開口——見
        // KinectSensorSender.OnNewQuestionDisplayed 說明，跟下面 OnQuestionAsked
        // 成對，避免這段合理的沉默被算成投入度低。
        kinectSensorSender?.OnNewQuestionDisplayed();

        // /session/start 回傳時 image_path 一定是空字串（見 StartRound 下方註解），
        // 圖片要等長者答完生圖前引導問題、/session/respond 才第一次真的生出來。
        if (!string.IsNullOrEmpty(resp.image_path))
            StartCoroutine(LoadPhoto(BuildImageUrl(resp.image_path)));

        var uris = new List<string>();
        uris.AddRange(LocalAudioPlayer.BuildUris(
            string.IsNullOrEmpty(resp.scene_audio_path) ? null : BuildAudioUrl(resp.scene_audio_path),
            new[] { resp.scene_audio_key }));
        uris.AddRange(LocalAudioPlayer.BuildUris(
            string.IsNullOrEmpty(resp.audio_path) ? null : BuildAudioUrl(resp.audio_path),
            new[] { resp.question_audio_key }));

        // 反應時間計時要等長者真的看完/聽完題目才開始，不是文字一顯示就
        // 開始——有語音的回合，語音播放的那幾秒鐘不該算進長者的反應時間；
        // 沒有語音的回合（第2、3回合）改用估算的閱讀時間頂替，不然沒語音
        // 的回合會變成完全不排除呈現時間，反而比有語音的回合更不公平
        // （2026-09-06 稽核，見 LocalAudioPlayer.EstimateReadingSeconds 說明）。
        if (uris.Count > 0)
            StartCoroutine(LocalAudioPlayer.PlaySequence(audioSource, uris, () => kinectSensorSender?.OnQuestionAsked()));
        else
            StartCoroutine(LocalAudioPlayer.DelayedAction(
                LocalAudioPlayer.EstimateReadingSeconds(aiText.text),
                () => kinectSensorSender?.OnQuestionAsked()));
    }

    IEnumerator LoadPhoto(string imageUrl)
    {
        using var req = UnityWebRequestTexture.GetTexture(imageUrl);
        yield return req.SendWebRequest();
        if (req.result == UnityWebRequest.Result.Success)
            photoDisplay.texture = DownloadHandlerTexture.GetContent(req);
        else
            Debug.LogWarning($"[Photo] 圖片載入失敗: {req.error}");
    }

    string BuildImageUrl(string serverPath)
    {
        const string prefix = "/media/images/";
        int idx = serverPath.IndexOf(prefix);
        string relative = idx >= 0 ? serverPath.Substring(idx + prefix.Length) : serverPath.TrimStart('/');
        return $"{backendUrl}/images/{relative}";
    }

    // ─── TTS 語音播放 ─────────────────────────────────────────────

    string BuildAudioUrl(string serverPath)
    {
        const string prefix = "/media/audio/";
        int idx = serverPath.IndexOf(prefix);
        string relative = idx >= 0 ? serverPath.Substring(idx + prefix.Length) : serverPath.TrimStart('/');
        return $"{backendUrl}/audio/{relative}";
    }

    void OnReplayAudio()
    {
        if (replayButton != null && !replayButton.interactable) return;
        if (audioSource == null || audioSource.clip == null) return;
        audioSource.Stop();
        audioSource.Play();
    }

    void OnSubmit()
    {
        if (!submitButton.interactable) return;

        if (pendingConfirmedText != null)
        {
            // 治療師已經確認（可能編輯過）這一題的回答，但 LLM 分類／RAG／生圖
            // 延後到現在才觸發（2026-09-06 改版，見 pendingConfirmedText 欄位
            // 說明）——跟長者直接送出走同一條路徑（SendResponse），後端
            // /session/respond 會用 pending_review 存的原始回答時間算反應時間，
            // 不會把治療師審核＋這段等待也算進去（見 session.py 說明）。
            string text = pendingConfirmedText;
            pendingConfirmedText = null;
            StartCoroutine(ProcessConfirmedSubmit(text));
            return;
        }

        // 沒有待套用的治療師確認結果：走舊路徑，給「沒辨識到文字」或
        // RequestReview 重試用盡退回舊流程這兩種邊界情況用。
        StartCoroutine(ProcessSubmit());
    }

    // 治療師確認過（可能編輯過）的回答，長者按下送出後才真的呼叫
    // /session/respond 觸發生成（2026-09-06 改版，見 pendingConfirmedText
    // 欄位說明）——刻意跟 ProcessSubmit 走同一條 SendResponse 路徑，不再
    // 另外維護一份「直接套用已算好結果」的邏輯：一來 SendResponse 本來就
    // 有正確的 loadingSpinner／generatingImageText 清除邏輯（舊版
    // ApplyConfirmedResponse 沒有，導致治療師反映轉圈圈永遠不會消失的
    // 問題），二來這樣兩條路徑（長者直接送出／治療師審核後送出）共用同一份
    // 生成與清理邏輯，之後修 bug 不用兩邊各修一次。
    IEnumerator ProcessConfirmedSubmit(string confirmedText)
    {
        isSubmitting = true;
        RefreshSubmitButton();
        ResetInputText();
        aiText.gameObject.SetActive(false);
        loadingSpinner.SetActive(true);

        yield return StartCoroutine(SendResponse(confirmedText));

        isSubmitting = false;
        RefreshSubmitButton();
    }

    IEnumerator ProcessSubmit()
    {
        isSubmitting = true;
        if (isRecording) StopRecording();
        isWaitingForStt = false;
        if (sttTimeoutCoroutine != null) { StopCoroutine(sttTimeoutCoroutine); sttTimeoutCoroutine = null; }
        RefreshSubmitButton();

        string userSpeech = displayedText;

        ResetInputText();
        aiText.gameObject.SetActive(false);
        loadingSpinner.SetActive(true);

        yield return StartCoroutine(SendResponse(userSpeech));

        isSubmitting = false;
        RefreshSubmitButton();
    }

    IEnumerator SendResponse(string elderResponse)
    {
        if (currentState == null)
        {
            loadingSpinner.SetActive(false);
            if (generatingImageText != null) generatingImageText.SetActive(false);
            aiText.gameObject.SetActive(true);
            yield break;
        }

        var body = new RespondRequest { elder_response = elderResponse, state = currentState };
        byte[] payload = Encoding.UTF8.GetBytes(JsonUtility.ToJson(body));

        // 2026-08-26稽核（使用者發現）：這支請求一旦失敗（網路抖動/逾時），
        // currentState 完全沒有機會更新，下一次送出還是帶著同一份舊的
        // question_number——後端可能把答案配對到錯的題號，甚至讓題號序列
        // 整個錯位（見 app/routers/session.py _fill_round_exchange_answer／
        // next_qn 計算說明）。原本只有「顯示一次錯誤」沒有重試，長者這句
        // 話就此消失。改成原地重試幾次（間隔1.5秒），大部分暫時性的網路
        // 抖動這樣就能救回來，只有重試用盡才真的放棄顯示錯誤。
        const int maxAttempts = 3;
        string responseText = null;
        for (int attempt = 1; attempt <= maxAttempts; attempt++)
        {
            using var req = new UnityWebRequest($"{backendUrl}/session/respond", "POST");
            req.uploadHandler   = new UploadHandlerRaw(payload);
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");
            AuthService.AttachAuthHeader(req);
            yield return req.SendWebRequest();

            if (req.result == UnityWebRequest.Result.Success)
            {
                responseText = req.downloadHandler.text;
                break;
            }

            Debug.LogWarning($"[Respond] API 第{attempt}次失敗: {req.error}");
            if (attempt < maxAttempts)
                yield return new WaitForSeconds(1.5f);
        }

        loadingSpinner.SetActive(false);
        if (generatingImageText != null) generatingImageText.SetActive(false);

        if (responseText == null)
        {
            Debug.LogError($"[Respond] API 重試{maxAttempts}次後仍失敗，長者這句回答暫時無法送出");
            aiText.gameObject.SetActive(true);
            yield break;
        }

        var resp = JsonUtility.FromJson<RespondResponse>(responseText);
        yield return StartCoroutine(ApplyFinalResponse(resp));
    }

    // 從 SendResponse 抽出來的尾段：拿到 /session/respond 的回應後，套用到畫面上
    // （更新題目、載入照片、播音檔）。2026-09-06 改版後，不管是長者直接送出、
    // 還是治療師審核確認後長者再按送出（ProcessConfirmedSubmit），都是走
    // SendResponse 呼叫這裡，不再有第二個進入點直接拿已算好的 resp 跳過 API。
    IEnumerator ApplyFinalResponse(RespondResponse resp)
    {
        // 長者按送出、真正進到下一題/下一回合了，麥克風才重新解鎖——鎖定期間
        // （等待治療師審核／已經在看治療師確認結果）不能讓長者提前開始新錄音，
        // 避免跟 pendingConfirmedText 打架（見 HandleSTTMessage 的 confirmed_text 分支）。
        if (!isPaused) micButton.interactable = true;

        if (resp.action == "end_session")
        {
            PlayerPrefs.SetString("ClosingThanks", resp.thanks_text ?? "");
            PlayerPrefs.SetString("ClosingQuestion", resp.question ?? "");
            // PlayerPrefs 沒有陣列型別，key 本身不含 '|'（都是英數字+底線的
            // audio_bank.py key 名稱），用它當分隔符安全串成一個字串，
            // ShareController 讀出來後用同一個字元切回陣列。
            PlayerPrefs.SetString("ClosingThanksAudioKeys", JoinAudioKeys(resp.thanks_audio_keys));
            PlayerPrefs.SetString("ClosingQuestionAudioKeys", JoinAudioKeys(resp.question_audio_keys));
            AuthSession.SessionId = sessionId;
            PlayerPrefs.SetString("NextScene", "ShareScene");
            currentRound = 1;
            SceneManager.LoadScene("LoadingScene");
            yield break;
        }

        if (resp.action == "end_round")
        {
            currentRound = resp.next_round > 0 ? resp.next_round : currentRound + 1;
            UpdateRoundBadge();
            if (resp.next_round > 0)
                StartCoroutine(StartRound(resp.next_round));
            yield break;
        }

        currentState = resp.state;
        aiText.text = BuildAiText(resp.scene_text, resp.question);
        aiText.gameObject.SetActive(true);
        // 同 ApplyRoundResponse：新問題（追問/下一題）剛顯示，先標記還沒進入
        // 回答等待期。
        kinectSensorSender?.OnNewQuestionDisplayed();

        // /session/start、/session/round 回傳時 image_path 一定是空字串（見
        // ApplyRoundResponse 上方註解），圖片是長者答完生圖前引導問題、這支
        // /session/respond 才第一次真的生出來，所以載入圖片要放在這裡，不是
        // ApplyRoundResponse。
        if (!string.IsNullOrEmpty(resp.image_path))
            StartCoroutine(LoadPhoto(BuildImageUrl(resp.image_path)));

        var uris = new List<string>();
        uris.AddRange(LocalAudioPlayer.BuildUris(
            string.IsNullOrEmpty(resp.scene_audio_path) ? null : BuildAudioUrl(resp.scene_audio_path),
            new[] { resp.scene_audio_key }));
        uris.AddRange(LocalAudioPlayer.BuildUris(
            string.IsNullOrEmpty(resp.audio_path) ? null : BuildAudioUrl(resp.audio_path),
            new[] { resp.question_audio_key }));

        // 反應時間計時要等長者真的看完/聽完題目才開始，理由同 ApplyRoundResponse。
        if (uris.Count > 0)
            StartCoroutine(LocalAudioPlayer.PlaySequence(audioSource, uris, () => kinectSensorSender?.OnQuestionAsked()));
        else
            StartCoroutine(LocalAudioPlayer.DelayedAction(
                LocalAudioPlayer.EstimateReadingSeconds(aiText.text),
                () => kinectSensorSender?.OnQuestionAsked()));
    }

    static string JoinAudioKeys(string[] keys)
    {
        if (keys == null || keys.Length == 0) return "";
        return string.Join("|", keys);
    }

    // ─── 長者未回應（治療師端「跳過」觸發，見 skip_scene）───────────────

    IEnumerator AutoSubmitNoResponse()
    {
        isSubmitting = true;
        RefreshSubmitButton();
        ResetInputText();
        aiText.gameObject.SetActive(false);
        loadingSpinner.SetActive(true);

        yield return StartCoroutine(SendResponse(NoResponseMarker));

        isSubmitting = false;
        RefreshSubmitButton();
    }

    void UpdateRoundBadge()
    {
        roundNumber.text = currentRound.ToString();
        roundTitle.text  = roundNames[currentRound - 1];
        roundSub.text    = $"ROUND {currentRound} / {totalRounds}";
    }

    void OnDestroy()
    {
        isQuitting = true;
        if (!UseKinect && isRecording)
            Microphone.End(micDevice);
        ws?.Close();
    }

    // ─── JSON 資料結構 ─────────────────────────────────────────────
    // StartRoundResponse / SessionStateData 定義在 SessionModels.cs，
    // 供 InstructionController 預抓第一回合資料時共用同一組型別。

    [System.Serializable]
    class RespondResponse
    {
        public string action;
        public string scene_text;
        // action=="scene_ready"：長者剛答完生圖前的引導問題，這裡才第一次真的
        // 生出圖片（見 app/routers/session.py session_respond 的同一段說明）。
        public string image_path;
        public string scene_audio_path;
        public string scene_audio_key;
        // thanks_audio_keys／question_audio_keys：只有 action=="end_session"
        // （心得環節開場）才會有值，見 app/services/closing_templates.py
        // build_closing_invitation——這兩段固定句是前端內建預錄音檔。
        public string thanks_text;
        public string[] thanks_audio_keys;
        public string question;
        public string audio_path;
        public string question_audio_key;
        public string[] question_audio_keys;
        public int next_round;
        public SessionStateData state;
    }

    [System.Serializable]
    class RespondRequest
    {
        public string elder_response;
        public SessionStateData state;
    }
}
