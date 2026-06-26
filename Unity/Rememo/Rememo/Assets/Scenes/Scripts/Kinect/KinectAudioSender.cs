using System;
using System.IO;
using UnityEngine;
using WebSocketSharp;
using Windows.Kinect;

public class KinectAudioSender : MonoBehaviour
{
    [Header("WebSocket 設定")]
    public string serverUrl = "ws://localhost:8000/ws/audio";

    private WebSocket ws;
    private KinectSensor sensor;
    private AudioBeamFrameReader audioReader;
    private MemoryStream audioAccumulator = new MemoryStream();
    private const int CHUNK_SIZE = 3200;
    private bool isInitialized = false;

    void Start()
    {
        ws = new WebSocket(serverUrl);
        ws.OnOpen += (s, e) => Debug.Log("[Audio WS] 已連線");
        ws.OnError += (s, e) => Debug.LogError($"[Audio WS] 錯誤: {e.Message}");
        ws.OnClose += (s, e) => Debug.Log("[Audio WS] 已關閉");
        ws.ConnectAsync();
    }

    private void TryInitAudio()
    {
        if (isInitialized) return;

        KinectManager km = KinectManager.Instance;
        if (km == null || !km.IsInitialized()) return;

        Kinect2Interface sensorInterface = km.GetSensorData().sensorInterface as Kinect2Interface;
        sensor = sensorInterface?.kinectSensor;
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

                byte[] int16Buffer = ConvertFloat32ToInt16(floatBuffer);
                audioAccumulator.Write(int16Buffer, 0, int16Buffer.Length);
                if (audioAccumulator.Length >= CHUNK_SIZE)
                {
                    if (ws?.ReadyState == WebSocketState.Open)
                        ws.SendAsync(audioAccumulator.ToArray(), null);
                    audioAccumulator.SetLength(0);
                }
            }
            frame.Dispose();
        }
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
            result[i * 2] = b[0];
            result[i * 2 + 1] = b[1];
        }
        return result;
    }

    void OnDestroy()
    {
        audioReader?.Dispose();
        audioReader = null;
        audioAccumulator?.Dispose();
        ws?.Close();
    }
}