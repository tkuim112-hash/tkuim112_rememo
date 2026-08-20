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
    // Kinect 手部追蹤本身會有單幀抖動，raycast 偶爾會在同一顆按鈕上短暫掃到
    // hitButton==null 又立刻掃回來——如果只憑單一幀的「沒掃到」就解除上面的
    // debounce，等於防呆完全沒用（長者手其實沒真的移開，抖一下就被當成「已離開」，
    // 2秒後照樣又被自動點一次）。改成要連續離開累計滿 AwayDebounceTime 秒才真正
    // 解除，過濾掉這種瞬間抖動（2026-08 稽核：實測到手沒移開卻仍收到空白錄音，
    // 就是這個單幀判定漏洞造成的）。
    private float _awayFromClickedTimer = 0f;
    private const float AwayDebounceTime = 0.5f;
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

        if (hitButton == _justClickedButton && _justClickedButton != null)
        {
            // 手還留在剛觸發過的按鈕上，先不要重新累計停留時間，避免同一次停留
            // 又被自動判定成第二次點擊；等手移到別的按鈕或完全移開累計滿
            // AwayDebounceTime 秒才解除。
            _awayFromClickedTimer = 0f;
            ResetRing();
        }
        else
        {
            if (_justClickedButton != null)
            {
                _awayFromClickedTimer += Time.deltaTime;
                if (_awayFromClickedTimer >= AwayDebounceTime)
                    _justClickedButton = null;
            }

            if (hitButton != null)
            {
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

                if (_hoverTimer >= hoverDuration && _justClickedButton == null)
                {
                    _justClickedButton = _currentButton;
                    _awayFromClickedTimer = 0f;
                    _currentButton.onClick.Invoke();
                    ResetRing();
                    if (_cursorRemapper != null)
                        _cursorRemapper.ResetToCorner();
                }
            }
            else
            {
                ResetRing();
            }
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