using System;
using System.Collections;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;

/// <summary>
/// 呼叫後端 /auth/login，供 LoginScreenController / LoginFailController 共用。
/// </summary>
public static class AuthService
{
    [System.Serializable]
    private class LoginRequestBody
    {
        public string email;
        public string password;
    }

    [System.Serializable]
    public class LoginResponseBody
    {
        public string token;
        public int therapist_id;
        public string name;
        public int organization_id;
    }

    public static IEnumerator Login(
        string backendUrl,
        string email,
        string password,
        Action<LoginResponseBody> onSuccess,
        Action<string> onFail)
    {
        var body = new LoginRequestBody { email = email, password = password };
        using var req = new UnityWebRequest($"{backendUrl}/auth/login", "POST");
        req.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(JsonUtility.ToJson(body)));
        req.downloadHandler = new DownloadHandlerBuffer();
        req.SetRequestHeader("Content-Type", "application/json");

        yield return req.SendWebRequest();

        if (req.result != UnityWebRequest.Result.Success)
        {
            string message = req.responseCode == 401
                ? "電子信箱或密碼錯誤"
                : $"登入失敗：{req.error}";
            onFail?.Invoke(message);
            yield break;
        }

        var resp = JsonUtility.FromJson<LoginResponseBody>(req.downloadHandler.text);
        onSuccess?.Invoke(resp);
    }
}
