using System;
using System.Collections.Generic;
using UnityEngine;
using WebSocketSharp;

public class KinectSkeletonSender : MonoBehaviour
{
    [Header("WebSocket 設定")]
    public string serverUrl = "wss://api.re-memo.com/ws/skeleton";

    private WebSocket ws;
    private KinectManager kinectManager;

    void Start()
    {
        // 後端從未實作 /ws/skeleton（骨架姿態分析已改由 /sensor/emotion 的
        // skel_* 欄位負責，見該端點），這裡停用連線，避免每次啟動都白連
        // 線失敗。ws 維持 null，Update()/OnDestroy() 既有的 null 檢查會
        // 自然讓下面的邏輯整段不執行。
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