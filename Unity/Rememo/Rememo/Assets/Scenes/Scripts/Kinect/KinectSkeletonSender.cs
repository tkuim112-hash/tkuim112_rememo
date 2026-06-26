using System;
using System.Collections.Generic;
using UnityEngine;
using WebSocketSharp;

public class KinectSkeletonSender : MonoBehaviour
{
    [Header("WebSocket 設定")]
    public string serverUrl = "ws://localhost:8000/ws/skeleton";

    private WebSocket ws;
    private KinectManager kinectManager;

    void Start()
    {
        kinectManager = KinectManager.Instance;

        ws = new WebSocket(serverUrl);
        ws.OnOpen  += (s, e) => Debug.Log("[Skeleton WS] 已連線");
        ws.OnError += (s, e) => Debug.LogError($"[Skeleton WS] 錯誤: {e.Message}");
        ws.OnClose += (s, e) => Debug.Log("[Skeleton WS] 已關閉");
        ws.ConnectAsync();
    }

    void Update()
    {
        if (kinectManager == null || !kinectManager.IsInitialized()) return;
        if (ws == null || ws.ReadyState != WebSocketState.Open) return;

        long userId = kinectManager.GetPrimaryUserID();
        if (userId == 0) return;

        var joints = new Dictionary<string, float[]>();

        for (int j = 0; j < 25; j++)
        {
            if (kinectManager.IsJointTracked(userId, j))
            {
                Vector3 pos = kinectManager.GetJointPosition(userId, j);
                joints[((KinectInterop.JointType)j).ToString()] = new float[]
                {
                    pos.x, pos.y, pos.z
                };
            }
        }

        string payload = JsonUtility.ToJson(new SkeletonPayload
        {
            type      = "skeleton",
            timestamp = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            userId    = userId.ToString(),
            jointKeys = new List<string>(joints.Keys).ToArray(),
            jointX    = GetValues(joints, 0),
            jointY    = GetValues(joints, 1),
            jointZ    = GetValues(joints, 2)
        });

        ws.SendAsync(payload, null);
    }

    float[] GetValues(Dictionary<string, float[]> joints, int axis)
    {
        var result = new float[joints.Count];
        int i = 0;
        foreach (var v in joints.Values)
            result[i++] = v[axis];
        return result;
    }

    void OnDestroy()
    {
        ws?.Close();
    }
}

[Serializable]
public class SkeletonPayload
{
    public string   type;
    public long     timestamp;
    public string   userId;
    public string[] jointKeys;
    public float[]  jointX;
    public float[]  jointY;
    public float[]  jointZ;
}