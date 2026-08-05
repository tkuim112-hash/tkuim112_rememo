using System;
using System.Collections;
using UnityEngine;
using UnityEngine.Networking;

/// <summary>
/// 呼叫後端 GET /session/pending，依 patient_id 換到跟治療師網頁共用的 session_id。
/// 必須在 WarmupScene 校正開始（KinectCalibrationManager.Start）之前完成，
/// 校正基準才會綁到治療師之後「啟動療程」用的同一個 session。
/// </summary>
public static class SessionService
{
    [System.Serializable]
    private class PendingSessionResponse
    {
        public string session_id;
    }

    [System.Serializable]
    private class SessionStatusResponse
    {
        public bool calibrated;
        public bool started;
    }

    public static IEnumerator FetchPendingSession(
        string backendUrl,
        string patientId,
        Action<string> onSuccess,
        Action<string> onFail)
    {
        using var req = UnityWebRequest.Get($"{backendUrl}/session/pending?patient_id={patientId}");
        AuthService.AttachAuthHeader(req);

        yield return req.SendWebRequest();

        if (req.result != UnityWebRequest.Result.Success)
        {
            onFail?.Invoke($"取得 session_id 失敗：{req.error}");
            yield break;
        }

        var resp = JsonUtility.FromJson<PendingSessionResponse>(req.downloadHandler.text);
        onSuccess?.Invoke(resp?.session_id ?? "");
    }

    /// <summary>
    /// 查詢治療師是否已按下「啟動療程」（即後端 /session/start 是否已被呼叫過）。
    /// 供 WarmupController 在校正完成後 poll，等治療師端啟動才解鎖本地開始按鈕。
    /// </summary>
    public static IEnumerator FetchStatus(
        string backendUrl,
        string sessionId,
        Action<bool> onSuccess,
        Action<string> onFail)
    {
        using var req = UnityWebRequest.Get($"{backendUrl}/session/{sessionId}/status");
        AuthService.AttachAuthHeader(req);

        yield return req.SendWebRequest();

        if (req.result != UnityWebRequest.Result.Success)
        {
            onFail?.Invoke($"取得療程狀態失敗：{req.error}");
            yield break;
        }

        var resp = JsonUtility.FromJson<SessionStatusResponse>(req.downloadHandler.text);
        onSuccess?.Invoke(resp?.started ?? false);
    }
}
