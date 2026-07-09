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
    public string backendUrl = "http://localhost:8000";
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
    public string sttServerUrl = "ws://localhost:8000/ws/stt";
    public int sampleRate = 16000;
    public int maxRecordSeconds = 60;

    [Header("逐字動畫")]
    public float charInterval = 0.03f;

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
    private readonly Queue<string> incomingMessages = new Queue<string>();
    private readonly object queueLock = new object();
    private string displayedText = "";
    private Coroutine typingCoroutine;

    private bool UseKinect => kinectAudioSender != null;

    [System.Serializable]
    private class ControlPayload { public string type; }

    [System.Serializable]
    private class STTMessage { public string type; public string text; public bool isFinal; public string action; }

    void Start()
    {
        submitButton.onClick.AddListener(OnSubmit);
        if (micButton != null) micButton.onClick.AddListener(OnMicToggle);
        if (replayButton != null) replayButton.onClick.AddListener(OnReplayAudio);
        ResetInputText();
        UpdateRoundBadge();
        loadingSpinner.SetActive(false);

        if (!UseKinect)
            ConnectWebSocket();

        StartCoroutine(StartRound(currentRound));
    }

    // ─── STT ──────────────────────────────────────────────────────

    void ConnectWebSocket()
    {
        ws = new WebSocket(AuthService.AppendToken(sttServerUrl));
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
        isRecording = true;
        if (typingCoroutine != null) StopCoroutine(typingCoroutine);
        displayedText = "";
        inputText.text = "錄音中...";
        inputText.color = new Color(1f, 0.4f, 0.4f, 1f);
        if (micButtonImage != null) micButtonImage.color = new Color(1f, 0.3f, 0.3f, 1f);
        RefreshSubmitButton();

        if (UseKinect)
        {
            kinectAudioSender.OnSttMessage = OnKinectSttMessage;
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
                    if (typingCoroutine != null) StopCoroutine(typingCoroutine);
                    typingCoroutine = StartCoroutine(TypeCharByChar(aiText.text));
                    break;
                case "skip_scene":
                    if (currentRound >= totalRounds)
                        SceneManager.LoadScene("ShareScene");
                    else
                        StartCoroutine(StartRound(currentRound + 1));
                    break;
                case "pause":
                    isPaused = true;
                    micButton.interactable = false;
                    submitButton.interactable = false;
                    break;
                case "resume":
                    isPaused = false;
                    RefreshSubmitButton();
                    micButton.interactable = true;
                    break;
                case "end":
                    Application.Quit();
                    break;
            }
            return;
        }

        if (msg.type != "transcript") return;
        if (typingCoroutine != null) StopCoroutine(typingCoroutine);
        typingCoroutine = StartCoroutine(TypeCharByChar(msg.text));
        if (msg.isFinal)
        {
            OnSttFinal();
            if (!string.IsNullOrWhiteSpace(msg.text))
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

    IEnumerator TypeCharByChar(string target)
    {
        inputText.color = new Color(0.2f, 0.2f, 0.2f, 1f);
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
            inputText.text = target;
            displayedText  = target;
        }
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
        currentState = resp.state;

        loadingSpinner.SetActive(false);
        aiText.text = resp.question;
        aiText.gameObject.SetActive(true);
        kinectSensorSender?.OnQuestionAsked();

        StartCoroutine(LoadPhoto(BuildImageUrl(resp.image_path)));
        if (!string.IsNullOrEmpty(resp.audio_path))
            StartCoroutine(PlayTTS(BuildAudioUrl(resp.audio_path)));
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

    IEnumerator PlayTTS(string audioUrl)
    {
        using var req = UnityWebRequestMultimedia.GetAudioClip(audioUrl, AudioType.WAV);
        yield return req.SendWebRequest();

        if (req.result != UnityWebRequest.Result.Success)
        {
            Debug.LogWarning($"[TTS] 語音載入失敗: {req.error}");
            yield break;
        }

        if (audioSource == null) yield break;
        AudioClip clip = DownloadHandlerAudioClip.GetContent(req);
        audioSource.Stop();
        audioSource.clip = clip;
        audioSource.Play();
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
            PlayerPrefs.SetString("ClosingQuestion", resp.question ?? "");
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
        if (!string.IsNullOrEmpty(resp.audio_path))
            StartCoroutine(PlayTTS(BuildAudioUrl(resp.audio_path)));
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

    [System.Serializable]
    class StartRoundResponse
    {
        public string user_name;
        public string today_topic;
        public string scene_text;
        public string image_path;
        public string question;
        public string audio_path;
        public SessionStateData state;
    }

    [System.Serializable]
    class RespondResponse
    {
        public string action;
        public string scene_text;
        public string question;
        public string audio_path;
        public int next_round;
        public SessionStateData state;
    }

    [System.Serializable]
    class RespondRequest
    {
        public string elder_response;
        public SessionStateData state;
    }

    [System.Serializable]
    class SessionStateData
    {
        public string user_id;
        public string session_id;
        public int round;
        public string[] scene_elements;
        public string[] covered_w;
        public string[] skipped_w;
        public string last_question_type;
        public string last_w_asked;
    }
}
