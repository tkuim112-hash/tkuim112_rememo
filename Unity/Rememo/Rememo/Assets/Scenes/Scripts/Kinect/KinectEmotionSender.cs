using System;
using UnityEngine;
using WebSocketSharp;
using Windows.Kinect;
using Microsoft.Kinect.Face;

public class KinectEmotionSender : MonoBehaviour
{
    public float LastHappy       { get; private set; }
    public float LastLookingAway { get; private set; }
    public float LastMouthMoved  { get; private set; }

    [Header("WebSocket 設定")]
    public string serverUrl = "ws://localhost:8000/ws/emotion";

    [Header("傳送設定")]
    public float sendInterval = 0.1f;

    private WebSocket       ws;
    private KinectSensor    sensor;
    private BodyFrameReader bodyReader;
    private FaceFrameSource faceSource;
    private FaceFrameReader faceReader;
    private Body[]          bodies;
    private float           sendTimer = 0f;

    private const int BODY_COUNT = 6;

    void Start()
    {
        ws = new WebSocket(serverUrl);
        ws.OnOpen  += (s, e) => Debug.Log("[Emotion WS] 已連線");
        ws.OnError += (s, e) => Debug.LogError($"[Emotion WS] 錯誤: {e.Message}");
        ws.OnClose += (s, e) => Debug.Log("[Emotion WS] 已關閉");
        ws.ConnectAsync();

        sensor = KinectSensor.GetDefault();
        if (sensor == null)
        {
            Debug.LogError("[Emotion] 找不到 Kinect 感測器");
            return;
        }

        var features = FaceFrameFeatures.Happy
                     | FaceFrameFeatures.LeftEyeClosed
                     | FaceFrameFeatures.RightEyeClosed
                     | FaceFrameFeatures.LookingAway
                     | FaceFrameFeatures.MouthOpen
                     | FaceFrameFeatures.MouthMoved;

        bodyReader = sensor.BodyFrameSource.OpenReader();
        bodies     = new Body[BODY_COUNT];

        faceSource = FaceFrameSource.Create(sensor, 0, features);
        faceReader = faceSource.OpenReader();

        if (!sensor.IsOpen) sensor.Open();
    }

    void Update()
    {
        // Throttle
        sendTimer += Time.deltaTime;
        if (sendTimer < sendInterval) return;
        sendTimer = 0f;

        if (ws == null || ws.ReadyState != WebSocketState.Open) return;

        // Step 1：找第一個被追蹤的身體，失效才重新指定
        using (var frame = bodyReader?.AcquireLatestFrame())
        {
            if (frame != null)
            {
                frame.GetAndRefreshBodyData(bodies);

                if (!faceSource.IsTrackingIdValid)
                {
                    foreach (var body in bodies)
                    {
                        if (body != null && body.IsTracked)
                        {
                            faceSource.TrackingId = body.TrackingId;
                            break;
                        }
                    }
                }
            }
        }

        // Step 2：讀取表情並送出
        using (var faceFrame = faceReader?.AcquireLatestFrame())
        {
            if (faceFrame == null || !faceFrame.IsTrackingIdValid) return;

            var result = faceFrame.FaceFrameResult;
            if (result == null) return;

            LastHappy = result.FaceProperties[FaceProperty.Happy] == DetectionResult.Yes   ? 1f :
                        result.FaceProperties[FaceProperty.Happy] == DetectionResult.Maybe ? 0.5f : 0f;

            LastLookingAway = result.FaceProperties[FaceProperty.LookingAway] == DetectionResult.Yes   ? 1f :
                              result.FaceProperties[FaceProperty.LookingAway] == DetectionResult.Maybe ? 0.5f : 0f;

            LastMouthMoved = result.FaceProperties[FaceProperty.MouthMoved] == DetectionResult.Yes   ? 1f :
                             result.FaceProperties[FaceProperty.MouthMoved] == DetectionResult.Maybe ? 0.5f : 0f;

            string GetState(DetectionResult r) => r.ToString();

            string payload = JsonUtility.ToJson(new EmotionPayload
            {
                type           = "emotion",
                timestamp      = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                happy          = GetState(result.FaceProperties[FaceProperty.Happy]),
                leftEyeClosed  = GetState(result.FaceProperties[FaceProperty.LeftEyeClosed]),
                rightEyeClosed = GetState(result.FaceProperties[FaceProperty.RightEyeClosed]),
                lookingAway    = GetState(result.FaceProperties[FaceProperty.LookingAway]),
                mouthOpen      = GetState(result.FaceProperties[FaceProperty.MouthOpen]),
                mouthMoved     = GetState(result.FaceProperties[FaceProperty.MouthMoved])
            });

            ws.SendAsync(payload, null);
        }
    }

    void OnDestroy()
    {
        faceReader?.Dispose();
        faceSource?.Dispose(true);
        bodyReader?.Dispose();
        ws?.Close();
        sensor?.Close();
    }
}

[Serializable]
public class EmotionPayload
{
    public string type;
    public long   timestamp;
    public string happy;
    public string leftEyeClosed;
    public string rightEyeClosed;
    public string lookingAway;
    public string mouthOpen;
    public string mouthMoved;
}