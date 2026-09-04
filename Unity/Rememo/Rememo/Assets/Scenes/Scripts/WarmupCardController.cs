using System.Collections;
using System.Collections.Generic;
using System.Text;
using TMPro;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.SceneManagement;
using UnityEngine.UI;

public class WarmupCardController : MonoBehaviour
{
    public enum PoseType
    {
        None,
        HoldArmsRaised,
        HoldTouchKnees,
        HoldArmStretch,
        CountLegLift,
        CountChestExpand,
        CountTouchKnees,
        CountWaistTwist,
        CountArmCircle,
    }

    [System.Serializable]
    public class ActionCard
    {
        [Tooltip("卡片的穩定識別碼，回報給後端後治療師網頁會用這個字串去對照它自己本機存的動作圖片/文字，兩邊各自的圖檔不用一致，只有這個字串要跟前端的對照表一致。同一個 poseType（例如原地踏步跟踢腿都是 CountLegLift）可能對應不同卡片，不能拿 poseType 當識別碼")]
        public string cardKey;

        public Sprite image;

        [Tooltip("只用在 poseType 是 None 的卡：顯示幾秒後自動換下一題。有設定 poseType 的卡一定要偵測到動作才會換，這個欄位不會生效")]
        public float durationSeconds = 15f;

        public PoseType poseType = PoseType.None;

        [Tooltip("只用在 HoldXxx 類型：姿勢要連續保持幾秒才算完成，對應卡片圖片上寫的秒數")]
        public float requiredHoldSeconds = 5f;

        [Tooltip("只用在 CountXxx 類型：要做滿幾次")]
        public int requiredCount = 1;

        [Tooltip("只用在 CountLegLift：腳掌高度差要超過腿長的幾倍才算抬腳，原地踏步動作幅度比踢腿小，門檻建議調低")]
        public float legLiftThreshold = 0.15f;

        [Tooltip("進度文字顯示用，不影響實際判定。IsLegLifted 不分左右腳，單腳抬放一次就 +1，但卡片上寫的次數是「左右各踏一次」的直覺次數，所以畫面顯示的次數 = repCount / 這個值。原地踏步這種單腳算兩次的卡設 2，其他卡維持 1（不影響顯示）")]
        public int displayCountDivisor = 1;
    }

    [Header("動作卡題庫，會從裡面隨機抽 5 張、不重複")]
    public ActionCard[] cardPool;

    [Header("後端設定")]
    [Tooltip("換卡時把目前 cardKey 回報給後端，供治療師網頁同步顯示同一張卡（本地圖片，見 ActionCard.cardKey 說明）")]
    public string backendUrl = "https://api.re-memo.com";

    [Tooltip("同一張卡進行中，隔多久回報一次目前做到幾次/幾秒給後端一次。網頁目前只用來畫一跳一跳的進度條（換卡才跳格），但這個即時進度資料先持續回報留著，之後要做卡片內即時填色可以直接用")]
    public float progressReportInterval = 0.3f;

    [Header("UI 元件")]
    public Image cardImage;

    [Tooltip("換動作、做對動作時播放音效用的 AudioSource")]
    public AudioSource audioSource;

    [Tooltip("換到下一張卡時播放的音效（要自己拖音效檔進來，這裡沒有預設檔案）")]
    public AudioClip cardChangeSound;

    [Tooltip("做對動作、卡片過關時播放的音效（要自己拖音效檔進來，這裡沒有預設檔案）")]
    public AudioClip successSound;

    [Tooltip("做對動作時，右側灰色框外側要閃一下的成功特效圖片")]
    public Image successGlowImage;

    [Tooltip("成功特效閃一次要花多久")]
    public float successGlowDuration = 0.6f;

    [Tooltip("右側「偵測中」徽章的 Image 元件，做對動作時會暫時換成 Completed Badge Sprite")]
    public Image detectionBadgeImage;

    [Tooltip("做對動作時，右側徽章要換成的圖片（要自己準備一張「已完成」的圖，這裡沒有預設檔案）")]
    public Sprite completedBadgeSprite;

    [Tooltip("顯示「做到第幾次／需要幾次」的進度文字，只有 CountXxx 類型的卡會更新，Hold/None 類型的卡會清空隱藏")]
    public TMP_Text progressText;

    [Tooltip("CountXxx 類型：關節資料常常會抖動雜訊，張開/收回都要連續穩定這麼多秒才算數，避免抖一下就誤判成一次")]
    public float countStableSeconds = 0.3f;

    [Tooltip("HoldXxx 類型：姿勢要連續消失超過這麼多秒，累積的保持時間才會歸零，避免單幀的雜訊抖動把進度洗掉")]
    public float holdDropoutTolerance = 0.5f;

    private List<ActionCard> selectedCards;
    private int currentIndex;
    private string sessionId;
    private float progressReportTimer;

    private float holdTimer;
    private float holdDropoutTimer;
    private int repCount;
    private bool confirmedExtended;
    private float extendedTimer;
    private float retractedTimer;
    private bool lastRawExtended; // 除錯用：確認過原因了記得拿掉

    // 換到新卡片時，長者可能上一張卡的動作還沒收回來，一開始就已經是「打開」
    // 狀態——這不是這張卡自己做出來的，不該算數。一定要先親眼見過一次穩定的
    // 「收合」，才允許開始確認「打開」，擋掉延續上一張卡動作的誤算。
    private bool hasSeenRetractedThisCard;

    // 過關音效還沒播完前，NextCard() 是延遲呼叫的，這段等待期間舊卡片仍是目前
    // 的卡片，Update() 還是會繼續判定動作。長者做完最後一次動作後姿勢常常沒有
    // 馬上收回，可能又被判定成完成一次，導致 CompleteCurrentCard() 重複觸發、
    // 音效響兩次。這個旗標擋掉同一張卡的重複觸發。
    private bool cardCompleted;

    // CountArmCircle 專用：手掌相對肩膀的角度是持續累加的量，跟其他 CountXxx
    // 用的「單一布林值進/出」狀態機不一樣，左右手各自獨立累加。
    private float armAngleLastLeft, armAngleAccumLeft;
    private float armAngleLastRight, armAngleAccumRight;
    private bool armAngleInitLeft, armAngleInitRight;
    private float lastArmCircleRepTime;

    // 一開始就記住「偵測中」原本的圖片，做對動作換成 completedBadgeSprite 一段
    // 時間後，才知道要換回哪一張。
    private Sprite detectingBadgeSprite;

    void Start()
    {
        sessionId = AuthSession.SessionId;

        if (detectionBadgeImage != null)
        {
            detectingBadgeSprite = detectionBadgeImage.sprite;
        }

        selectedCards = DrawRandomCards(cardPool, 5);
        currentIndex = 0;
        ShowCurrentCard();

        // KinectManager 是跨場景常駐的，它「自動把場景裡的 AvatarController 登記進
        // 追蹤名單」這個機制只在 App 剛啟動時跑一次。如果同一次 App 執行期間，第二位
        // 長者又走到這個場景一次，角色不會被重新登記，動作就會完全沒反應、也不會報
        // 錯，很難發現。這裡改成每次進場景都主動登記一次。
        var kinectManager = KinectManager.Instance;
        var avatarController = FindFirstObjectByType<AvatarController>();
        if (kinectManager != null && avatarController != null &&
            !kinectManager.avatarControllers.Contains(avatarController))
        {
            kinectManager.avatarControllers.Add(avatarController);
        }
    }

    List<ActionCard> DrawRandomCards(ActionCard[] pool, int count)
    {
        var shuffled = new List<ActionCard>(pool);
        for (int i = shuffled.Count - 1; i > 0; i--)
        {
            int j = Random.Range(0, i + 1);
            (shuffled[i], shuffled[j]) = (shuffled[j], shuffled[i]);
        }
        return shuffled.GetRange(0, Mathf.Min(count, shuffled.Count));
    }

    void ShowCurrentCard()
    {
        var card = selectedCards[currentIndex];
        cardImage.sprite = card.image;

        if (audioSource != null && cardChangeSound != null)
        {
            audioSource.PlayOneShot(cardChangeSound);
        }

        holdTimer = 0f;
        holdDropoutTimer = 0f;
        repCount = 0;
        confirmedExtended = false;
        extendedTimer = 0f;
        retractedTimer = 0f;
        lastRawExtended = false;
        hasSeenRetractedThisCard = false;
        cardCompleted = false;
        armAngleAccumLeft = 0f;
        armAngleAccumRight = 0f;
        armAngleInitLeft = false;
        armAngleInitRight = false;
        lastArmCircleRepTime = -999f;
        progressReportTimer = 0f;

        CancelInvoke(nameof(NextCard));
        if (card.poseType == PoseType.None)
        {
            // 沒有動作偵測的卡才用倒數計時自動換題；有 poseType 的卡一定要
            // 偵測到動作才會換下一張，不會自動跳過。
            Invoke(nameof(NextCard), card.durationSeconds);
        }

        UpdateProgressText();
        ReportWarmupProgress(card);
    }

    static bool IsCountType(PoseType poseType) =>
        poseType == PoseType.CountLegLift || poseType == PoseType.CountChestExpand ||
        poseType == PoseType.CountTouchKnees || poseType == PoseType.CountWaistTwist ||
        poseType == PoseType.CountArmCircle;

    static bool IsHoldType(PoseType poseType) =>
        poseType == PoseType.HoldArmsRaised || poseType == PoseType.HoldTouchKnees ||
        poseType == PoseType.HoldArmStretch;

    // 目前這張卡做到多少百分比（0~1）。網頁目前只用一跳一跳的進度條（換卡才
    // 跳格，不吃這個值），但先把即時進度持續回報留著，供之後要做卡片內填色
    // 時直接沿用。None 類型（沒有動作偵測）沒有進度可算，固定回 0。
    float ComputeProgressRatio(ActionCard card)
    {
        if (IsCountType(card.poseType))
            return card.requiredCount > 0 ? Mathf.Clamp01((float)repCount / card.requiredCount) : 0f;
        if (IsHoldType(card.poseType))
            return card.requiredHoldSeconds > 0 ? Mathf.Clamp01(holdTimer / card.requiredHoldSeconds) : 0f;
        return 0f;
    }

    // 把目前這張卡的 cardKey／進度回報給後端，讓治療師網頁能同步顯示同一張卡
    // （用它自己本機存的圖片，見 ActionCard.cardKey 說明）。sessionId 拿不到
    // （離線 demo、換取 pending session 失敗）就不回報，不擋長者端的暖身
    // 流程——這支只是給治療師看的旁路資訊，失敗也不影響長者實際做動作。
    void ReportWarmupProgress(ActionCard card)
    {
        if (string.IsNullOrEmpty(sessionId) || string.IsNullOrEmpty(card.cardKey)) return;
        StartCoroutine(PostWarmupProgress(card.cardKey, currentIndex + 1, selectedCards.Count, ComputeProgressRatio(card)));
    }

    IEnumerator PostWarmupProgress(string cardKey, int cardIndex, int totalCards, float progressRatio)
    {
        var payload = new WarmupProgressPayload
        {
            card_key = cardKey,
            card_index = cardIndex,
            total_cards = totalCards,
            progress_ratio = progressRatio,
        };
        byte[] body = Encoding.UTF8.GetBytes(JsonUtility.ToJson(payload));
        using var req = new UnityWebRequest($"{backendUrl}/session/{sessionId}/warmup_progress", "POST");
        req.uploadHandler = new UploadHandlerRaw(body);
        req.downloadHandler = new DownloadHandlerBuffer();
        req.SetRequestHeader("Content-Type", "application/json");
        AuthService.AttachAuthHeader(req);
        yield return req.SendWebRequest();
        if (req.result != UnityWebRequest.Result.Success)
            Debug.LogWarning($"[Warmup] 回報進度失敗: {req.error}");
    }

    // 顯示進度：CountXxx 類型顯示「做到第幾次／需要幾次」，HoldXxx 類型顯示
    // 「撐了幾秒／需要幾秒」，None 類型（沒有動作偵測、純倒數換題）沒有進度可
    // 顯示，清空隱藏文字。
    void UpdateProgressText()
    {
        if (progressText == null) return;

        var card = selectedCards[currentIndex];

        if (IsCountType(card.poseType))
        {
            int divisor = Mathf.Max(1, card.displayCountDivisor);
            progressText.text = $"{repCount / divisor} / {card.requiredCount / divisor}";
        }
        else if (IsHoldType(card.poseType))
        {
            progressText.text = $"{holdTimer:F1} / {card.requiredHoldSeconds:F0} 秒";
        }
        else
        {
            progressText.text = "";
        }
    }

    void Update()
    {
        if (selectedCards == null || currentIndex >= selectedCards.Count) return;

        var card = selectedCards[currentIndex];
        if (card.poseType == PoseType.None) return;

        // 過關音效還沒播完、還沒真的換到下一張卡之前，這張卡已經完成了，不要再
        // 繼續判定動作——不然次數/秒數會一直往上跳過目標值，進度文字也會跟著
        // 一直變。
        if (cardCompleted) return;

        bool userTracked = KinectPoseChecker.TryGetTrackedUser(out var km, out var userId);

        // 使用者短暫沒被追蹤到（感測器一瞬間跟丟）跟「有追蹤到、但姿勢沒對」一樣，都
        // 交給 UpdateHold/UpdateCount 的防抖動邏輯處理，不在這裡直接把進度歸零。
        switch (card.poseType)
        {
            case PoseType.HoldArmsRaised:
                UpdateHold(userTracked && KinectPoseChecker.IsArmsRaisedSideways(km, userId), card.requiredHoldSeconds);
                break;
            case PoseType.CountLegLift:
                // 長者踏步節奏快，一腳還沒完全放平、下一腳就抬起來了，「兩腳打平」
                // 停留常常不到 0.3 秒，全域的 countStableSeconds 太嚴，收合永遠沒
                // 辦法確認。這張卡的收合穩定時間單獨縮短。
                UpdateCount(userTracked && KinectPoseChecker.IsLegLifted(km, userId, card.legLiftThreshold), card.requiredCount,
                    stableSeconds: 0.1f);
                break;
            case PoseType.CountChestExpand:
                // 動作做快一點時，打開/收合常常沒撐滿全域的 0.3 秒，那一次就會被
                // 吃掉、漏算。跟原地踏步一樣單獨縮短穩定時間。擴胸的幅度門檻
                // （手肘要撐開到 sw*1.2）本來就比較不容易被身體晃動誤觸發，縮短
                // 時間應該比原地踏步安全。
                UpdateCount(userTracked && KinectPoseChecker.IsChestExpandOpen(km, userId), card.requiredCount,
                    stableSeconds: 0.1f);
                break;
            case PoseType.CountTouchKnees:
                // 摸膝蓋是唯一一張改成「碰到當下就算數」的卡：長者摸到膝蓋後如果沒
                // 意識到要站直，會一直卡在等放開穩定，感覺很久才過關。穩定時間原本
                // 壓到 0.05 秒是為了接住快速輕觸，但實測發現手停在門檻邊緣附近時，
                // 0.05 秒連邊緣抖動都濾不掉，卡片一開始就送出兩次計數。改回 0.1 秒，
                // 跟原地踏步、擴胸一致。
                UpdateCount(userTracked && KinectPoseChecker.IsTouchingKnees(km, userId), card.requiredCount,
                    countOnExtend: true, stableSeconds: 0.1f);
                break;
            case PoseType.CountWaistTwist:
                UpdateCount(userTracked && KinectPoseChecker.IsWaistTwisted(km, userId), card.requiredCount);
                break;
            case PoseType.CountArmCircle:
                if (userTracked) UpdateArmCircle(km, userId, card.requiredCount);
                break;
        }

        UpdateProgressText();

        // 卡片進行中持續回報進度（節流，避免每一幀都打一次 API）。網頁目前
        // 只用來畫一跳一跳的進度條，但先把即時進度資料留著回報。
        progressReportTimer += Time.deltaTime;
        if (progressReportTimer >= progressReportInterval)
        {
            progressReportTimer = 0f;
            ReportWarmupProgress(card);
        }
    }

    void UpdateHold(bool poseMatched, float requiredHoldSeconds)
    {
        if (poseMatched)
        {
            holdDropoutTimer = 0f;

            // 跟 UpdateCount 一樣：這張卡開始時如果動作剛好還維持著上一張卡結束前
            // 的姿勢（比如手臂旋轉剛做完，手都還沒放下就換到手臂平舉），不該直接
            // 開始計時。一定要先親眼見過一次穩定的「沒做動作」，才開始累積時間。
            if (hasSeenRetractedThisCard)
            {
                holdTimer += Time.deltaTime;
                if (holdTimer >= requiredHoldSeconds)
                {
                    CompleteCurrentCard();
                }
            }
        }
        else
        {
            holdDropoutTimer += Time.deltaTime;
            if (holdDropoutTimer >= holdDropoutTolerance)
            {
                holdTimer = 0f;
                hasSeenRetractedThisCard = true;
            }
        }
    }

    // countOnExtend：預設 false，維持原本「打開+收合都做完才 +1」的行為。只有
    // 摸膝蓋傳 true——碰到膝蓋、穩定 0.3 秒就直接算數，不用等放開；放開穩定後
    // 只是重新武裝，讓下一次碰觸可以再被算一次，不會因為停在碰觸姿勢就一直
    // 重複加。
    // stableSeconds：null 時用全域的 countStableSeconds（0.3 秒）。原地踏步/踢腿
    // 傳 0.1 秒——長者踏步節奏快，兩腳打平的停留常常不到 0.3 秒，全域門檻太嚴會
    // 讓收合永遠沒辦法確認，repCount 卡住不動。
    void UpdateCount(bool isExtendedRaw, int requiredCount, bool countOnExtend = false, float? stableSeconds = null)
    {
        float stable = stableSeconds ?? countStableSeconds;

        // 除錯用：原始訊號（還沒套穩定門檻前）一翻轉就印一次，確認過原因了記得拿掉。
        if (isExtendedRaw != lastRawExtended)
        {
            lastRawExtended = isExtendedRaw;
            Debug.Log($"[Raw:{selectedCards[currentIndex].poseType}] 翻轉為 {(isExtendedRaw ? "打開" : "收合")} t={Time.time:F2} extendedTimer={extendedTimer:F2} retractedTimer={retractedTimer:F2}");
        }

        if (isExtendedRaw)
        {
            extendedTimer += Time.deltaTime;
            retractedTimer = 0f;
        }
        else
        {
            retractedTimer += Time.deltaTime;
            extendedTimer = 0f;
        }

        // 這張卡開始後，只要親眼見過一次穩定的收合，就代表接下來的「打開」是這張
        // 卡自己做出來的，不是延續上一張卡結束前的姿勢。
        if (retractedTimer >= stable)
        {
            hasSeenRetractedThisCard = true;
        }

        if (hasSeenRetractedThisCard && !confirmedExtended && extendedTimer >= stable)
        {
            confirmedExtended = true;

            if (countOnExtend)
            {
                repCount++;
                Debug.Log($"[UpdateCount:{selectedCards[currentIndex].poseType}] 確認打開，+1。repCount={repCount}/{requiredCount}"); // 除錯用：確認過原因了記得拿掉
                if (repCount >= requiredCount)
                {
                    CompleteCurrentCard();
                }
            }
            else
            {
                Debug.Log($"[UpdateCount:{selectedCards[currentIndex].poseType}] 確認打開，等收合。目前 repCount={repCount}"); // 除錯用：確認過原因了記得拿掉
            }
        }
        else if (confirmedExtended && retractedTimer >= stable)
        {
            confirmedExtended = false;

            if (countOnExtend)
            {
                Debug.Log($"[UpdateCount:{selectedCards[currentIndex].poseType}] 確認收合，重新武裝。repCount={repCount}/{requiredCount}"); // 除錯用：確認過原因了記得拿掉
                return;
            }

            repCount++;
            Debug.Log($"[UpdateCount:{selectedCards[currentIndex].poseType}] 確認收合，+1。repCount={repCount}/{requiredCount}"); // 除錯用：確認過原因了記得拿掉
            if (repCount >= requiredCount)
            {
                CompleteCurrentCard();
            }
        }
    }

    // 手臂旋轉：累加手掌相對肩膀的「有方向性」角度變化量，任一手臂累積轉滿 360
    // 度（同一個方向）就算完成一次循環。用帶正負號的角度變化去加總，不是取絕對
    // 值——這樣手臂稍微抖動（一下順時針一下逆時針）的雜訊會正負互相抵消，只有
    // 持續往同一方向轉才會真的累積到 360 度，避免抖動被誤判成轉完一圈。
    void UpdateArmCircle(KinectManager km, long userId, int requiredCount)
    {
        bool completedLeft = TrackArmCircle(km, userId, true, ref armAngleLastLeft, ref armAngleAccumLeft, ref armAngleInitLeft);
        bool completedRight = TrackArmCircle(km, userId, false, ref armAngleLastRight, ref armAngleAccumRight, ref armAngleInitRight);

        // 卡片畫的是兩手同時一起畫圈，左右手幾乎會同時各自轉滿 360 度，如果左右
        // 手各自都能 +1，一圈會被算成兩次。加個短暫的冷卻時間，同一圈兩手前後腳
        // 完成只算一次；如果真的隔了一段時間才完成第二次，還是會正常各自計數。
        // 原本設 1 秒太保守——兩手同一圈完成的時間差應該遠小於這個值，轉快一點
        // 的人反而會被冷卻時間擋掉下一圈，改成 0.4 秒還是夠攔同一圈的重複計算。
        if ((completedLeft || completedRight) && Time.time - lastArmCircleRepTime > 0.4f)
        {
            lastArmCircleRepTime = Time.time;
            repCount++;
            if (repCount >= requiredCount)
            {
                CompleteCurrentCard();
            }
        }
    }

    bool TrackArmCircle(KinectManager km, long userId, bool isLeft, ref float lastAngle, ref float accumulated, ref bool initialized)
    {
        float? angleOpt = KinectPoseChecker.GetHandAngleAroundShoulder(km, userId, isLeft);
        if (!angleOpt.HasValue)
        {
            return false;
        }

        float angle = angleOpt.Value;

        if (!initialized)
        {
            lastAngle = angle;
            initialized = true;
            return false;
        }

        accumulated += Mathf.DeltaAngle(lastAngle, angle);
        lastAngle = angle;

        if (Mathf.Abs(accumulated) >= 360f)
        {
            accumulated = 0f;
            return true;
        }

        return false;
    }

    void CompleteCurrentCard()
    {
        // 等音效播完才換卡的這段期間，舊卡片仍會繼續被判定，避免長者姿勢還沒收
        // 回來又被重複觸發一次完成。
        if (cardCompleted) return;
        cardCompleted = true;
        // 過關那一刻先送一次 100% 進度，確保最後一小段進度不會被跳過。
        ReportWarmupProgress(selectedCards[currentIndex]);

        if (audioSource != null && successSound != null)
        {
            audioSource.PlayOneShot(successSound);
        }

        if (successGlowImage != null)
        {
            StopCoroutine(nameof(FlashSuccessGlow));
            StartCoroutine(nameof(FlashSuccessGlow));
        }

        if (detectionBadgeImage != null && completedBadgeSprite != null)
        {
            detectionBadgeImage.sprite = completedBadgeSprite;
            StopCoroutine(nameof(RestoreDetectionBadge));
            StartCoroutine(nameof(RestoreDetectionBadge));
        }

        // 有設定過關音效的話，等音效播完才換卡，不要音效還在響、畫面就已經跳到
        // 下一題。沒設定音效（或沒接 AudioSource）就維持原本立刻換卡的行為。
        CancelInvoke(nameof(NextCard));
        float delay = (audioSource != null && successSound != null) ? successSound.length : 0f;
        if (delay > 0f)
        {
            Invoke(nameof(NextCard), delay);
        }
        else
        {
            NextCard();
        }
    }

    // 「已完成」圖片顯示 successGlowDuration 秒後，換回原本的「偵測中」圖片。
    IEnumerator RestoreDetectionBadge()
    {
        yield return new WaitForSeconds(successGlowDuration);
        detectionBadgeImage.sprite = detectingBadgeSprite;
    }

    // 成功特效：alpha 從 0 淡入到 1 再淡出回 0，用 Sin 曲線讓一進一出比較平滑。
    IEnumerator FlashSuccessGlow()
    {
        Color c = successGlowImage.color;

        for (float t = 0f; t < successGlowDuration; t += Time.deltaTime)
        {
            c.a = Mathf.Sin((t / successGlowDuration) * Mathf.PI);
            successGlowImage.color = c;
            yield return null;
        }

        c.a = 0f;
        successGlowImage.color = c;
    }

    void NextCard()
    {
        currentIndex++;
        if (currentIndex >= selectedCards.Count)
        {
            // KinectManager 是跨場景常駐的，但這裡的角色只活在 WarmupGameScene，切場景
            // 時會被銷毀。不先把它從 KinectManager 的 avatarControllers 名單上移除，
            // KinectManager 下一幀還是會想更新一個已經不存在的物件，丟出
            // MissingReferenceException。只移除這個角色自己，不要整批清空，避免影響
            // 到其他場景可能有的角色。
            var kinectManager = KinectManager.Instance;
            var avatarController = FindFirstObjectByType<AvatarController>();
            if (kinectManager != null && avatarController != null)
            {
                kinectManager.avatarControllers.Remove(avatarController);
            }

            // 5 張暖身動作卡都做完了，接著跟 WarmupController.OnStart() 原本的行為
            // 一樣直接切去 InstructionScene，不經過 LoadingScene，避免長者多等一段
            // 讀取畫面。
            SceneManager.LoadScene("InstructionScene");
            return;
        }
        ShowCurrentCard();
    }
}

[System.Serializable]
public class WarmupProgressPayload
{
    public string card_key;
    public int card_index;
    public int total_cards;
    public float progress_ratio;
}
