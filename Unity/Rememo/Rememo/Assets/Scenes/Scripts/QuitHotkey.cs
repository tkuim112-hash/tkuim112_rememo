using UnityEngine;
using UnityEngine.InputSystem;

/// <summary>
/// 全域隱藏快捷鍵：按 Esc 離開程式。
/// 透過 RuntimeInitializeOnLoadMethod 在遊戲啟動時自動建立，不需要放進任何場景，
/// 且 DontDestroyOnLoad，所以每個場景都有效。
/// </summary>
public class QuitHotkey : MonoBehaviour
{
    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
    static void Bootstrap()
    {
        var go = new GameObject("QuitHotkey");
        DontDestroyOnLoad(go);
        go.AddComponent<QuitHotkey>();
    }

    void Update()
    {
        var kb = Keyboard.current;
        if (kb != null && kb.escapeKey.wasPressedThisFrame)
        {
#if UNITY_EDITOR
            UnityEditor.EditorApplication.isPlaying = false;
#else
            Application.Quit();
#endif
        }
    }
}
