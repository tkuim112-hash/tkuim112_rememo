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
        public bool requested;
        public bool warmup_ready;
        public bool started;
    }

    public static IEnumerator FetchPendingSession(
        string backendUrl,
        string patientId,
        Action<string> onSuccess,
        Action<string> onFail)
    {
        // source=unity 讓後端只在「Unity 真的選定病患」時才標記該病患活動中，
        // 治療師網頁自己開啟開始療程頁（不帶這個參數）不會觸發活動中徽章。
        using var req = UnityWebRequest.Get($"{backendUrl}/session/pending?patient_id={patientId}&source=unity");
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
    /// 查詢第一回合內容是否已經真正生成完畢（/session/start 已完整跑完 LLM 分類、
    /// RAG 檢索、TTS 合成）。供 InstructionScene poll，決定何時把內容拿回來、進場 GameScene。
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

    /// <summary>
    /// 查詢治療師是否已按下「啟動療程」（/session/start 已被呼叫，但不保證生成完畢）。
    /// 供 WarmupController 在校正完成後 poll，一偵測到就知道可以準備切去
    /// WarmupGameScene——但還要再等 FetchWarmupReady 確認治療師網頁真的載入
    /// 暖身頁面才會真的切，避免 Unity 搶先跑到治療師網頁前面。
    /// </summary>
    public static IEnumerator FetchRequested(
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
        onSuccess?.Invoke(resp?.requested ?? false);
    }

    /// <summary>
    /// 查詢治療師網頁的暖身頁面是否已經真的載入、開始 poll warmup_progress
    /// （見後端 session_warmup_progress_get 設定這個旗標的說明）。requested
    /// 只代表治療師按下了按鈕，網頁跳轉/掛載還需要一點時間，Unity 只看
    /// requested 就切場景的話，有機會比治療師網頁還早進暖身關卡，治療師端
    /// 還沒開始 polling 就已經錯過最前面幾張卡片的回報。WarmupController
    /// 在 requested 之後多等這個旗標，確保兩邊真正同步起跑。
    /// </summary>
    public static IEnumerator FetchWarmupReady(
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
        onSuccess?.Invoke(resp?.warmup_ready ?? false);
    }
}
