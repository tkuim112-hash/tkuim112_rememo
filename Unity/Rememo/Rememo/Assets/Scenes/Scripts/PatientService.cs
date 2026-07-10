using System;
using System.Collections;
using UnityEngine;
using UnityEngine.Networking;

/// <summary>
/// 呼叫後端 GET /patients，取得目前登入治療師所屬機構的病患清單，供 UserSelectScene 用。
/// </summary>
public static class PatientService
{
    [System.Serializable]
    public class PatientSummary
    {
        public int id;
        public string name;
        public int age;
        public string avatar;
    }

    [System.Serializable]
    private class PatientListResponse
    {
        public PatientSummary[] patients;
    }

    public static IEnumerator FetchPatients(
        string backendUrl,
        Action<PatientSummary[]> onSuccess,
        Action<string> onFail)
    {
        using var req = UnityWebRequest.Get($"{backendUrl}/patients");
        AuthService.AttachAuthHeader(req);

        yield return req.SendWebRequest();

        if (req.result != UnityWebRequest.Result.Success)
        {
            onFail?.Invoke($"取得病患清單失敗：{req.error}");
            yield break;
        }

        var resp = JsonUtility.FromJson<PatientListResponse>(req.downloadHandler.text);
        onSuccess?.Invoke(resp?.patients ?? new PatientSummary[0]);
    }
}
