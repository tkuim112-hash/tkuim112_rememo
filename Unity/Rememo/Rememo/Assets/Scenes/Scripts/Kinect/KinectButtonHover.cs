using UnityEngine;
using UnityEngine.UI;
using UnityEngine.EventSystems;
using System.Collections.Generic;

public class KinectButtonHover : MonoBehaviour
{
    [Header("懸停設定")]
    public float hoverDuration = 2f;

    [Header("UI 元件")]
    public Image progressRing;

    private float _hoverTimer = 0f;
    private Button _currentButton = null;
    // 剛觸發過點擊的按鈕：手沒有真的移開（hitButton 變成別顆或 null）之前，同一顆
    // 按鈕不能重新開始累計停留時間，否則長者按下麥克風後手還沒移開，會在
    // hoverDuration 秒後被系統自己再點一次「停止」，中間根本沒時間錄到語音
    // （2026-08 稽核：實測到回合1 STT 因此收到空白錄音）。
    private Button _justClickedButton = null;
    private RectTransform _rt;
    private Canvas _canvas;
    private HandCursorRemapper _cursorRemapper;

    void Start()
    {
        _rt = transform as RectTransform;
        _canvas = GetComponentInParent<Canvas>();
        _cursorRemapper = GetComponent<HandCursorRemapper>();
        Debug.Log($"[Hover] progressRing is null: {progressRing == null}");
        ResetRing();
    }

    void LateUpdate()
    {
        // ── 將 RectTransform 世界座標轉換為螢幕座標 ─────────
        Vector2 screenPos = RectTransformUtility.WorldToScreenPoint(
            _canvas.renderMode == RenderMode.ScreenSpaceOverlay ? null : _canvas.worldCamera,
            _rt.position
        );

        var pointer = new PointerEventData(EventSystem.current)
        {
            position = screenPos
        };

        var results = new List<RaycastResult>();
        EventSystem.current.RaycastAll(pointer, results);

        // ── 從所有命中結果中找 Button（含自身及父層）────────
        Button hitButton = null;
        foreach (var r in results)
        {
            // 跳過游標自身
            if (r.gameObject == gameObject) continue;

            // 先查自身
            hitButton = r.gameObject.GetComponent<Button>();
            if (hitButton != null) break;

            // 再查父層（往上最多 5 層）
            Transform t = r.gameObject.transform.parent;
            int depth = 0;
            while (t != null && depth < 5)
            {
                hitButton = t.GetComponent<Button>();
                if (hitButton != null) break;
                t = t.parent;
                depth++;
            }
            if (hitButton != null) break;
        }

        if (hitButton != null && hitButton == _justClickedButton)
        {
            // 手還留在剛觸發過的按鈕上，先不要重新累計停留時間，避免同一次停留
            // 又被自動判定成第二次點擊；等手移到別的按鈕或完全移開才解除。
            ResetRing();
        }
        else if (hitButton != null)
        {
            _justClickedButton = null;
            Debug.Log($"[Hover] 找到Button: {hitButton.gameObject.name}, timer:{_hoverTimer:F2}/{hoverDuration}");

            // 切換目標時重置計時
            if (_currentButton != hitButton)
            {
                ResetRing();
                _currentButton = hitButton;
                ShowRing(true);
            }

            _hoverTimer += Time.deltaTime;

            if (progressRing != null)
                progressRing.fillAmount = Mathf.Clamp01(_hoverTimer / hoverDuration);

            if (_hoverTimer >= hoverDuration)
            {
                _justClickedButton = _currentButton;
                _currentButton.onClick.Invoke();
                ResetRing();
                if (_cursorRemapper != null)
                    _cursorRemapper.ResetToCorner();
            }
        }
        else
        {
            _justClickedButton = null;
            ResetRing();
        }
    }

    void ResetRing()
    {
        _hoverTimer = 0f;
        _currentButton = null;
        ShowRing(false);

        if (progressRing != null)
            progressRing.fillAmount = 0f;
    }

    void ShowRing(bool visible)
    {
        if (progressRing != null)
            progressRing.gameObject.SetActive(visible);
    }
}