using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Networking;

/// <summary>
/// 播放「後端下載音檔」跟「前端內建預錄音檔（依 key）」混合組成的播放佇列。
///
/// 背景：app/services/audio_bank.py 把固定字串模板（非LLM即時生成的句子，
/// 例如16大主題的Q1邀請語、5W1H題庫、心得環節固定句）改成前端播放內建
/// 音檔，後端不再為這些句子即時呼叫TTS，只回傳一個 key；LLM 動態生成的
/// 內容則仍照舊由後端合成、回傳 audio_path 讓前端下載播放。同一輪回應可能
/// 兩種都有（例如生圖前Q1邀請語：「說到{今日主題}，」這段治療師自由輸入、
/// 沒辦法預錄，後端仍即時TTS這段短前綴，audio_path 是這段前綴；後半段
/// 固定邀請語改用 audio_key 對應的內建音檔，接在前綴播完後續播）。
///
/// 本地音檔（key）用 file:// URI 指向 StreamingAssets/Audio/{key}.wav，
/// 跟後端下載的 http(s):// URI 走同一套 UnityWebRequestMultimedia.
/// GetAudioClip 下載流程，不用另外維護一套本地播放邏輯。
/// </summary>
public static class LocalAudioPlayer
{
    /// <summary>
    /// tts_cache/ 的 wav 檔（含 sharing_*）要放進
    /// Assets/StreamingAssets/Audio/{key}.wav，檔名跟後端 audio_bank.py
    /// 的 key 一一對應。
    /// </summary>
    private const string AudioSubfolder = "Audio";

    public static string LocalKeyUri(string audioKey)
    {
        string path = System.IO.Path.Combine(Application.streamingAssetsPath, AudioSubfolder, audioKey + ".wav");
        return new System.Uri(path).AbsoluteUri;
    }

    /// <summary>
    /// 組出這一段要依序播放的 URI 清單：先放後端下載的 remoteAudioUrl（可能
    /// 是 null／空字串，代表這段沒有需要即時TTS的動態內容），再依序接上
    /// localAudioKeys 對應的內建音檔。呼叫端自己決定 remoteAudioUrl 用哪個
    /// 欄位組（例如 GameController.BuildAudioUrl(resp.audio_path)），這裡
    /// 只負責播放順序，不處理 URL 組裝細節，維持跟後端 host 設定無關。
    /// </summary>
    public static List<string> BuildUris(string remoteAudioUrl, IEnumerable<string> localAudioKeys)
    {
        var uris = new List<string>();
        if (!string.IsNullOrEmpty(remoteAudioUrl))
            uris.Add(remoteAudioUrl);
        if (localAudioKeys != null)
        {
            foreach (var key in localAudioKeys)
            {
                if (!string.IsNullOrEmpty(key))
                    uris.Add(LocalKeyUri(key));
            }
        }
        return uris;
    }

    /// <summary>
    /// 依序下載並播放 uris 裡的每一段音檔，播完一段才播下一段。任何一段
    /// 載入失敗只印警告、跳過那一段接著播下一段，不整串中斷——長者端寧可
    /// 少聽一句，也不要卡住整個流程（跟現有 PlayTTS 失敗時仍會呼叫
    /// StartReactionTimeout 同一種降級哲學）。
    /// </summary>
    public static IEnumerator PlaySequence(AudioSource audioSource, List<string> uris, System.Action onAllDone = null)
    {
        if (audioSource != null)
        {
            foreach (var uri in uris)
            {
                using var req = UnityWebRequestMultimedia.GetAudioClip(uri, AudioType.WAV);
                yield return req.SendWebRequest();

                if (req.result != UnityWebRequest.Result.Success)
                {
                    Debug.LogWarning($"[LocalAudioPlayer] 音檔載入失敗，跳過這一段: {uri} ({req.error})");
                    continue;
                }

                AudioClip clip = DownloadHandlerAudioClip.GetContent(req);
                audioSource.Stop();
                audioSource.clip = clip;
                audioSource.Play();
                yield return new WaitForSeconds(clip.length);
            }
        }
        onAllDone?.Invoke();
    }
}
