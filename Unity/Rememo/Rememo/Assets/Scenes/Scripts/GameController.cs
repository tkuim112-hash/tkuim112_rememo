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

    [Header("Kinect 整合")]
    [Tooltip("拖入場景中的 KinectAudioSender；若留空則退回使用內建麥克風")]
    public KinectAudioSender kinectAudioSender;
    [Tooltip("拖入場景中的 KinectSensorSender；若留空則不追蹤反應時間")]
    public KinectSensorSender kinectSensorSender;

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
    private Coroutine sttTimeoutCoroutine;
    private readonly WaitForSeconds sttTimeoutWait = new WaitForSeconds(5f);
    private Coroutine reactionTimeoutCoroutine;
    private readonly WaitForSeconds reactionTimeoutWait = new WaitForSeconds(30f);
    private const string NoResponseMarker = "（長者未回應）";
    private readonly Queue<string> incomingMessages = new Queue<string>();
    private readonly object queueLock = new object();
    private string displayedText = "";

    private bool UseKinect => kinectAudioSender != null;

    [System.Serializable]
    private class ControlPayload { public string type; }

    [System.Serializable]
    private class STTMessage { public string type; public string text; public bool isFinal; public string action; }

    void Start()
    {
        string selectedPatientId = PlayerPrefs.GetString("SelectedPatientId", "");
        if (!string.IsNullOrEmpty(selectedPatientId)) userId = selectedPatientId;

        // 跟 WarmupScene 的 KinectCalibrationManager 用同一組 session_id（由 SessionService
        // 在 UserSelectScene 選定病患時換好），校正資料才會跟這次療程的對話記錄綁在一起。
        string sharedSessionId = PlayerPrefs.GetString("session_id", "");
        if (!string.IsNullOrEmpty(sharedSessionId)) sessionId = sharedSessionId;

        submitButton.onClick.AddListener(OnSubmit);
        if (micButton != null) micButton.onClick.AddListener(OnMicToggle);
        if (replayButton != null) replayButton.onClick.AddListener(OnReplayAudio);
        ResetInputText();
        UpdateRoundBadge();
        loadingSpinner.SetActive(false);

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
        ws.OnError += (s, e) => Debug.LogError($"[Game STT WS] 錯誤: {e.Message}");
        ws.OnClose += (s, e) => Debug.Log("[Game STT WS] 已關閉");
        ws.OnMessage += (s, e) => {
            if (!e.IsText) return;
            lock (queueLock) incomingMessages.Enqueue(e.Data);
        };
        ws.ConnectAsync();
    }

    void OnMicToggle()
    {
        if (!isRecording) StartRecording();
        else              StopRecording();
    }

    void StartRecording()
    {
        CancelReactionTimeout();
        isRecording = true;
        displayedText = "";
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
        // 逾時前都沒收到任何 transcript 訊息，displayedText 還是空的：代表整段
        // 錄音沒辨識到任何內容，見 OnSttFinal 的 noSpeechRecognized 說明。
        OnSttFinal(noSpeechRecognized: string.IsNullOrEmpty(displayedText));
    }

    void OnSttFinal(bool noSpeechRecognized = false)
    {
        if (sttTimeoutCoroutine != null) { StopCoroutine(sttTimeoutCoroutine); sttTimeoutCoroutine = null; }
        isWaitingForStt = false;
        // 沒辨識到任何內容時，把卡住的「辨識中...」換成「辨識完成」，不要留著
        // 讓長者/治療師誤以為還在辨識；真的有辨識到文字的情況完全不動這裡，
        // 文字框已經在 HandleSTTMessage 被實際辨識結果蓋過了。
        if (noSpeechRecognized)
            inputText.text = "辨識完成";
        RefreshSubmitButton();
    }

    void RefreshSubmitButton()
    {
        bool enabled = !isRecording && !isWaitingForStt && !isSubmitting;
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
    }

    void StreamMicAudio()
    {
        int pos = Microphone.GetPosition(micDevice);
        if (pos < lastSamplePos) lastSamplePos = 0;
        int count = pos - lastSamplePos;
        if (count <= 0) return;
        float[] samples = new float[count];
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
        catch { return; }
        if (msg == null) return;

        if (msg.type == "control")
        {
            switch (msg.action)
            {
                case "replay_audio":
                    OnReplayAudio();
                    break;
                case "skip_scene":
                    // 跳過「目前這一題」，不是跳過整個回合：視同長者未回應直接進下一步，
                    // 沿用 ReactionTimeout 逾時時同樣的忙碌判斷，避免跟錄音/送出中互撞。
                    CancelReactionTimeout();
                    if (!isRecording && !isWaitingForStt && !isSubmitting && !isPaused)
                        StartCoroutine(AutoSubmitNoResponse());
                    break;
                case "pause":
                    isPaused = true;
                    CancelReactionTimeout();
                    micButton.interactable = false;
                    submitButton.interactable = false;
                    break;
                case "resume":
                    isPaused = false;
                    RefreshSubmitButton();
                    micButton.interactable = true;
                    StartReactionTimeout();
                    break;
                case "end":
                    Application.Quit();
                    break;
            }
            return;
        }

        if (msg.type != "transcript") return;
        bool hasText = !string.IsNullOrWhiteSpace(msg.text);
        if (hasText)
        {
            inputText.color = new Color(0.2f, 0.2f, 0.2f, 1f);
            inputText.text = msg.text;
            displayedText  = msg.text;
        }
        if (msg.isFinal)
        {
            OnSttFinal(noSpeechRecognized: !hasText);
            if (hasText)
                StartCoroutine(PostTranscript(msg.text));
        }
    }

    IEnumerator PostTranscript(string text)
    {
        byte[] body = Encoding.UTF8.GetBytes($"{{\"text\":{JsonUtility.ToJson(text)}}}");
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

    void ApplyRoundResponse(StartRoundResponse resp)
    {
        currentState = resp.state;
        aiText.text = resp.question;
        aiText.gameObject.SetActive(true);
        kinectSensorSender?.OnQuestionAsked();

        StartCoroutine(LoadPhoto(BuildImageUrl(resp.image_path)));

        var uris = new List<string>();
        uris.AddRange(LocalAudioPlayer.BuildUris(
            string.IsNullOrEmpty(resp.scene_audio_path) ? null : BuildAudioUrl(resp.scene_audio_path),
            new[] { resp.scene_audio_key }));
        uris.AddRange(LocalAudioPlayer.BuildUris(
            string.IsNullOrEmpty(resp.audio_path) ? null : BuildAudioUrl(resp.audio_path),
            new[] { resp.question_audio_key }));

        if (uris.Count > 0)
            StartCoroutine(LocalAudioPlayer.PlaySequence(audioSource, uris, StartReactionTimeout));
        else
            StartReactionTimeout();
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
        if (audioSource == null || audioSource.clip == null) return;
        audioSource.Stop();
        audioSource.Play();
    }

    void OnSubmit()
    {
        if (!submitButton.interactable) return;
        StartCoroutine(ProcessSubmit());
    }

    IEnumerator ProcessSubmit()
    {
        CancelReactionTimeout();
        isSubmitting = true;
        if (isRecording) StopRecording();
        isWaitingForStt = false;
        if (sttTimeoutCoroutine != null) { StopCoroutine(sttTimeoutCoroutine); sttTimeoutCoroutine = null; }
        RefreshSubmitButton();

        ResetInputText();
        aiText.gameObject.SetActive(false);
        loadingSpinner.SetActive(true);

        string userSpeech = displayedText;
        displayedText = "";

        yield return StartCoroutine(SendResponse(userSpeech));

        isSubmitting = false;
        RefreshSubmitButton();
    }

    IEnumerator SendResponse(string elderResponse)
    {
        if (currentState == null)
        {
            loadingSpinner.SetActive(false);
            aiText.gameObject.SetActive(true);
            yield break;
        }

        var body = new RespondRequest { elder_response = elderResponse, state = currentState };
        using var req = new UnityWebRequest($"{backendUrl}/session/respond", "POST");
        req.uploadHandler   = new UploadHandlerRaw(Encoding.UTF8.GetBytes(JsonUtility.ToJson(body)));
        req.downloadHandler = new DownloadHandlerBuffer();
        req.SetRequestHeader("Content-Type", "application/json");
        AuthService.AttachAuthHeader(req);
        yield return req.SendWebRequest();

        loadingSpinner.SetActive(false);

        if (req.result != UnityWebRequest.Result.Success)
        {
            Debug.LogError($"[Respond] API 失敗: {req.error}");
            aiText.gameObject.SetActive(true);
            yield break;
        }

        var resp = JsonUtility.FromJson<RespondResponse>(req.downloadHandler.text);

        if (resp.action == "end_session")
        {
            PlayerPrefs.SetString("ClosingText", resp.scene_text ?? "");
            PlayerPrefs.SetString("ClosingThanks", resp.thanks_text ?? "");
            PlayerPrefs.SetString("ClosingQuestion", resp.question ?? "");
            // PlayerPrefs 沒有陣列型別，key 本身不含 '|'（都是英數字+底線的
            // audio_bank.py key 名稱），用它當分隔符安全串成一個字串，
            // ShareController 讀出來後用同一個字元切回陣列。
            PlayerPrefs.SetString("ClosingSceneAudioKeys", JoinAudioKeys(resp.scene_audio_keys));
            PlayerPrefs.SetString("ClosingThanksAudioKeys", JoinAudioKeys(resp.thanks_audio_keys));
            PlayerPrefs.SetString("ClosingQuestionAudioKeys", JoinAudioKeys(resp.question_audio_keys));
            PlayerPrefs.SetString("session_id", sessionId);
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
        aiText.text = resp.question;
        aiText.gameObject.SetActive(true);
        kinectSensorSender?.OnQuestionAsked();

        var uris = new List<string>();
        uris.AddRange(LocalAudioPlayer.BuildUris(
            string.IsNullOrEmpty(resp.scene_audio_path) ? null : BuildAudioUrl(resp.scene_audio_path),
            new[] { resp.scene_audio_key }));
        uris.AddRange(LocalAudioPlayer.BuildUris(
            string.IsNullOrEmpty(resp.audio_path) ? null : BuildAudioUrl(resp.audio_path),
            new[] { resp.question_audio_key }));

        if (uris.Count > 0)
            StartCoroutine(LocalAudioPlayer.PlaySequence(audioSource, uris, StartReactionTimeout));
        else
            StartReactionTimeout();
    }

    static string JoinAudioKeys(string[] keys)
    {
        if (keys == null || keys.Length == 0) return "";
        return string.Join("|", keys);
    }

    // ─── 反應逾時（長者聽完問題30秒沒按麥克風）────────────────────────

    void CancelReactionTimeout()
    {
        if (reactionTimeoutCoroutine != null)
        {
            StopCoroutine(reactionTimeoutCoroutine);
            reactionTimeoutCoroutine = null;
        }
    }

    void StartReactionTimeout()
    {
        CancelReactionTimeout();
        reactionTimeoutCoroutine = StartCoroutine(ReactionTimeout());
    }

    IEnumerator ReactionTimeout()
    {
        yield return reactionTimeoutWait;
        reactionTimeoutCoroutine = null;
        if (isRecording || isWaitingForStt || isSubmitting || isPaused) yield break;
        StartCoroutine(AutoSubmitNoResponse());
    }

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
        public string scene_audio_path;
        public string scene_audio_key;
        // scene_audio_keys／thanks_audio_keys／question_audio_keys：只有
        // action=="end_session"（心得環節開場）才會有值，見
        // app/services/closing_templates.py build_closing_invitation——
        // 那三段固定句全部是前端內建預錄音檔，可能不只一個 key（例如
        // 承接語＋系統整合肯定是兩句拼接，要接續播放兩個音檔）。
        public string[] scene_audio_keys;
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
