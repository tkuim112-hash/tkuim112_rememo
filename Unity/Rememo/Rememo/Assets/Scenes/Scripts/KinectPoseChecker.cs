using UnityEngine;
using static KinectInterop.JointType;

// 這些門檻值都沒有拿真的 Kinect 裝置實測校正過，是憑關節幾何關係猜的合理起點。
// 使用者是 60~75 歲的長者，動作幅度、速度都可能比較保守，門檻故意抓寬鬆一點
// （寧可誤判過關，也不要長者明明有做動作卻一直卡關）。現場測試時如果誤判太
// 多，才調整這裡的比例係數（用肩寬當作身形的相對單位，而不是寫死公尺數，是
// 為了不同身高的人都還算適用）。
public static class KinectPoseChecker
{
    public static bool TryGetTrackedUser(out KinectManager km, out long userId)
    {
        km = KinectManager.Instance;
        userId = 0;
        if (km == null || !km.IsUserDetected())
        {
            return false;
        }
        userId = km.GetPrimaryUserID();
        return userId != 0;
    }

    public static float ShoulderWidth(KinectManager km, long userId)
    {
        Vector3 l = km.GetJointPosition(userId, (int)ShoulderLeft);
        Vector3 r = km.GetJointPosition(userId, (int)ShoulderRight);
        return Vector3.Distance(l, r);
    }

    public static bool IsArmsRaisedSideways(KinectManager km, long userId)
    {
        if (!km.IsJointTracked(userId, (int)HandLeft) || !km.IsJointTracked(userId, (int)HandRight) ||
            !km.IsJointTracked(userId, (int)ShoulderLeft) || !km.IsJointTracked(userId, (int)ShoulderRight))
        {
            return false;
        }

        float sw = ShoulderWidth(km, userId);
        if (sw < 0.05f) return false;

        Vector3 shL = km.GetJointPosition(userId, (int)ShoulderLeft);
        Vector3 shR = km.GetJointPosition(userId, (int)ShoulderRight);
        Vector3 hL = km.GetJointPosition(userId, (int)HandLeft);
        Vector3 hR = km.GetJointPosition(userId, (int)HandRight);

        // 垂直容忍度上下不對稱：長者手平舉常常撐不住、手會往下垂，比舉過高常見，
        // 所以手比肩膀低的容忍範圍故意放寬，手比肩膀高的還是抓原本的門檻。
        float vDiffL = shL.y - hL.y;
        float vDiffR = shR.y - hR.y;
        bool leftLevel = vDiffL < sw * 1.3f && vDiffL > -sw * 0.8f;
        bool rightLevel = vDiffR < sw * 1.3f && vDiffR > -sw * 0.8f;
        bool leftOut = Mathf.Abs(hL.x - shL.x) > sw * 0.4f;
        bool rightOut = Mathf.Abs(hR.x - shR.x) > sw * 0.4f;

        return leftLevel && rightLevel && leftOut && rightOut;
    }

    // 摸膝蓋碰到/放開的判定滯後狀態（hysteresis）：距離只有單一門檻的話，彎腰
    // 摸膝蓋時手在門檻附近小幅晃動（加上彎腰時肩寬本身也會飄動，門檻線跟著
    // 抖），會被反覆誤判成「放開又碰到」，一次彎腰動作就能多算好幾次（見
    // 2026-09-14 現場回報：實際彎 3 次被算成 5 次）。改成碰到／放開用不同門檻，
    // 距離要明顯拉開超過 TouchExitRatio 才算放開，抖動幅度不夠大就維持原本
    // 狀態，不會來回誤觸發。ResetTouchKneesState 在每次進新卡片時呼叫，避免
    // 上一張卡結束時的狀態延續到這一張。
    private const float TouchEnterRatio = 1.2f;
    private const float TouchExitRatio = 1.5f;
    private static bool touchKneesEngaged;

    public static void ResetTouchKneesState()
    {
        touchKneesEngaged = false;
    }

    public static bool IsTouchingKnees(KinectManager km, long userId)
    {
        if (!km.IsJointTracked(userId, (int)HandLeft) || !km.IsJointTracked(userId, (int)HandRight) ||
            !km.IsJointTracked(userId, (int)KneeLeft) || !km.IsJointTracked(userId, (int)KneeRight))
        {
            return touchKneesEngaged;
        }

        float sw = ShoulderWidth(km, userId);
        if (sw < 0.05f) return touchKneesEngaged;

        Vector3 hL = km.GetJointPosition(userId, (int)HandLeft);
        Vector3 hR = km.GetJointPosition(userId, (int)HandRight);
        Vector3 kL = km.GetJointPosition(userId, (int)KneeLeft);
        Vector3 kR = km.GetJointPosition(userId, (int)KneeRight);

        // 實測發現手自然垂放（站好不動、沒有伸手）距離膝蓋就已經只有肩寬的
        // 0.29~0.40 倍，原本 1.6 倍的門檻太寬鬆，站著不動就會被判定成「碰到」。
        // 改到 0.9 倍又太緊——彎腰時 Kinect 對肩膀的追蹤準確度會下降，量到的
        // 肩寬（當比例基準）本身就會飄動，伸手伸到底也量不到那麼低的比例。抓在
        // 兩次實測中間，改成 1.2 倍當作「碰到」的進入門檻。
        float distL = Vector3.Distance(hL, kL);
        float distR = Vector3.Distance(hR, kR);

        if (touchKneesEngaged)
        {
            // 已經判定碰到了：只要還有一手停在門檻附近，就當作長者還維持著碰
            // 膝蓋的姿勢，兩手都明顯拉開超過 TouchExitRatio 才算真的放開。
            touchKneesEngaged = distL < sw * TouchExitRatio || distR < sw * TouchExitRatio;
        }
        else
        {
            touchKneesEngaged = distL < sw * TouchEnterRatio && distR < sw * TouchEnterRatio;
        }

        // 除錯用：確認過原因了記得把這段 log 拿掉。
        if (Time.frameCount % 30 == 0)
        {
            Debug.Log($"[TouchKnees] distL={distL:F3} distR={distR:F3} sw={sw:F3} enter={sw * TouchEnterRatio:F3} exit={sw * TouchExitRatio:F3} touching={touchKneesEngaged}");
        }

        return touchKneesEngaged;
    }

    // 扭腰：實際動作是髖部左右擺動（不是上半身相對下半身扭轉），所以量「髖部
    // 中心」相對「肩膀中心」的左右（X 軸）偏移量，肩膀當作比較穩定不太會左右
    // 晃的參考點。用肩寬當比例基準，不分左右哪一邊擺都算。
    public static bool IsWaistTwisted(KinectManager km, long userId)
    {
        if (!km.IsJointTracked(userId, (int)ShoulderLeft) || !km.IsJointTracked(userId, (int)ShoulderRight) ||
            !km.IsJointTracked(userId, (int)HipLeft) || !km.IsJointTracked(userId, (int)HipRight))
        {
            return false;
        }

        float sw = ShoulderWidth(km, userId);
        if (sw < 0.05f) return false;

        Vector3 shL = km.GetJointPosition(userId, (int)ShoulderLeft);
        Vector3 shR = km.GetJointPosition(userId, (int)ShoulderRight);
        Vector3 hL = km.GetJointPosition(userId, (int)HipLeft);
        Vector3 hR = km.GetJointPosition(userId, (int)HipRight);

        float shoulderCenterX = (shL.x + shR.x) * 0.5f;
        float hipCenterX = (hL.x + hR.x) * 0.5f;

        return Mathf.Abs(hipCenterX - shoulderCenterX) > sw * 0.2f;
    }

    // 手臂旋轉：回傳手掌相對同側肩膀的角度（度，Atan2 的 -180~180），用來讓
    // WarmupCardController 自己去累加轉了多少角度、判斷有沒有畫完一整圈。這裡只
    // 回傳單一幀的角度（沒有狀態記憶），累加邏輯故意不放在這個純工具類別裡。
    public static float? GetHandAngleAroundShoulder(KinectManager km, long userId, bool isLeft)
    {
        var handJoint = isLeft ? HandLeft : HandRight;
        var shoulderJoint = isLeft ? ShoulderLeft : ShoulderRight;

        if (!km.IsJointTracked(userId, (int)handJoint) || !km.IsJointTracked(userId, (int)shoulderJoint))
        {
            return null;
        }

        Vector3 hand = km.GetJointPosition(userId, (int)handJoint);
        Vector3 shoulder = km.GetJointPosition(userId, (int)shoulderJoint);

        float dx = hand.x - shoulder.x;
        float dy = hand.y - shoulder.y;
        if (Mathf.Abs(dx) < 0.01f && Mathf.Abs(dy) < 0.01f) return null;

        return Mathf.Atan2(dy, dx) * Mathf.Rad2Deg;
    }

    // 踢腿、原地踏步都用同一個「單腳抬起」判斷，靠腳掌相對高度差；用腿長
    // （髖到腳掌的距離）當比例基準。門檻是可調參數，因為原地踏步的動作幅度
    // 天生比踢腿小，兩張卡各自用不同門檻（由 WarmupCardController 的
    // ActionCard.legLiftThreshold 指定），不用同一個標準互相牽制。
    // 原本量腳掌高度差，但腳掌是 Kinect 最末端、離鏡頭通常也最遠的關節，追蹤
    // 常常不穩定，實測時出現過瞬間跳到 0.4~0.5 公尺的雜訊尖峰。改量兩邊膝蓋的
    // 高度差——膝蓋離身體核心近，抬腿時擺動幅度也夠大，訊號比腳掌乾淨。腿長
    // 還是用髖到腳掌算，當作跟身高無關的比例基準（這只是取兩點的靜態距離，
    // 不像瞬時高度那樣容易被單一幀雜訊影響）。
    public static bool IsLegLifted(KinectManager km, long userId, float thresholdRatio = 0.15f)
    {
        if (!km.IsJointTracked(userId, (int)FootLeft) || !km.IsJointTracked(userId, (int)HipLeft) ||
            !km.IsJointTracked(userId, (int)KneeLeft) || !km.IsJointTracked(userId, (int)KneeRight))
        {
            if (Time.frameCount % 30 == 0)
            {
                Debug.Log($"[LegLift] 追蹤失敗 FootLeft={km.IsJointTracked(userId, (int)FootLeft)} HipLeft={km.IsJointTracked(userId, (int)HipLeft)} KneeLeft={km.IsJointTracked(userId, (int)KneeLeft)} KneeRight={km.IsJointTracked(userId, (int)KneeRight)}");
            }
            return false;
        }

        Vector3 fL = km.GetJointPosition(userId, (int)FootLeft);
        Vector3 kL = km.GetJointPosition(userId, (int)KneeLeft);
        Vector3 kR = km.GetJointPosition(userId, (int)KneeRight);
        Vector3 hipL = km.GetJointPosition(userId, (int)HipLeft);

        float legLength = Mathf.Max(0.3f, Vector3.Distance(hipL, fL));
        float diff = Mathf.Abs(kL.y - kR.y);
        bool lifted = diff > legLength * thresholdRatio;

        // 除錯用：確認過原因了記得把這段 log 拿掉。
        if (Time.frameCount % 30 == 0)
        {
            Debug.Log($"[LegLift] kneeDiff={diff:F3} legLength={legLength:F3} threshold={legLength * thresholdRatio:F3} lifted={lifted}");
        }

        return lifted;
    }

    // 原本是各自看「單一手肘」到脊椎中心的水平距離，但這張卡的動作是手一路舉在
    // 頭部兩側、手肘左右擺，休息姿勢的手肘本來就已經離中心夠遠，導致整個過程
    // 可能一路判定為「已打開」、永遠等不到收合，repCount 卡死在 0。改成看兩手肘
    // 之間的距離，「打開（手肘往外撐）」跟「收合（手肘互相靠近）」的對比才明確，
    // 也不用再依賴 SpineMid（手靠近頭部時容易被手臂擋住、追蹤不穩）。
    public static bool IsChestExpandOpen(KinectManager km, long userId)
    {
        if (!km.IsJointTracked(userId, (int)ElbowLeft) || !km.IsJointTracked(userId, (int)ElbowRight))
        {
            return false;
        }

        float sw = ShoulderWidth(km, userId);
        if (sw < 0.05f) return false;

        Vector3 eL = km.GetJointPosition(userId, (int)ElbowLeft);
        Vector3 eR = km.GetJointPosition(userId, (int)ElbowRight);

        // 實測發現卡片教的「手舉頭側」預備姿勢，手肘距離就已經到肩寬的 1.3 倍
        // 左右，只是舉手、還沒做擴胸動作就會被判定成「打開」。門檻拉到 1.7 倍，
        // 跟預備姿勢（1.3倍）留出明顯margin，真正做滿擴胸動作量到的 2.6 倍左右
        // 還是遠遠超過，不會變成做不到。
        float diff = Mathf.Abs(eL.x - eR.x);
        bool open = diff > sw * 1.7f;

        // 除錯用：確認過原因了記得把這段 log 拿掉。
        if (Time.frameCount % 30 == 0)
        {
            Debug.Log($"[ChestExpand] elbowDiff={diff:F3} sw={sw:F3} threshold={sw * 1.7f:F3} open={open}");
        }

        return open;
    }

    // ────────────────────────────────────────────────────────────────────
    // 以下是給暖身活動評估指標用的「連續值」量測函式，不影響上面既有的過關
    // 判斷（那些門檻已經調校過，這裡刻意分開，不要互相牽動）。回傳角度給
    // WarmupCardController 累積成關節角度達成率／左右對稱性（Robinson,
    // Herzog & Nigg 1987 Symmetry Index：SI=(Xr-Xl)/(0.5*(|Xr|+|Xl|))*100）。
    // 詳見暖身活動評估指標.md／synthetic-zooming-pizza 計畫。
    // ────────────────────────────────────────────────────────────────────

    // 手臂平舉：肩外展角度。0 度＝手臂垂放身側，90 度＝手臂平舉與肩同高
    // （這張卡的目標角度），180 度＝手臂完全舉過頭。用「垂直落差」跟「水平
    // 外伸距離」的反正切算，不需要知道手臂實際長度。
    static float AbductionAngle(Vector3 shoulder, Vector3 hand)
    {
        float verticalDrop = shoulder.y - hand.y; // 手比肩膀低時為正
        float horizontalOut = new Vector2(hand.x - shoulder.x, hand.z - shoulder.z).magnitude;
        return Mathf.Atan2(horizontalOut, verticalDrop) * Mathf.Rad2Deg;
    }

    public static bool TryGetArmRaiseAngles(KinectManager km, long userId, out float leftDeg, out float rightDeg)
    {
        leftDeg = rightDeg = 0f;
        if (!km.IsJointTracked(userId, (int)HandLeft) || !km.IsJointTracked(userId, (int)HandRight) ||
            !km.IsJointTracked(userId, (int)ShoulderLeft) || !km.IsJointTracked(userId, (int)ShoulderRight))
        {
            return false;
        }

        leftDeg = AbductionAngle(km.GetJointPosition(userId, (int)ShoulderLeft), km.GetJointPosition(userId, (int)HandLeft));
        rightDeg = AbductionAngle(km.GetJointPosition(userId, (int)ShoulderRight), km.GetJointPosition(userId, (int)HandRight));
        return true;
    }

    // 踢腿／原地踏步：髖屈曲角度。0 度＝腿垂直站立，角度越大代表膝蓋抬得
    // 越高（往前抬腿）。跟肩外展角度同一套反正切算法，只是換成髖到膝蓋。
    public static bool TryGetHipFlexionAngles(KinectManager km, long userId, out float leftDeg, out float rightDeg)
    {
        leftDeg = rightDeg = 0f;
        if (!km.IsJointTracked(userId, (int)HipLeft) || !km.IsJointTracked(userId, (int)HipRight) ||
            !km.IsJointTracked(userId, (int)KneeLeft) || !km.IsJointTracked(userId, (int)KneeRight))
        {
            return false;
        }

        leftDeg = AbductionAngle(km.GetJointPosition(userId, (int)HipLeft), km.GetJointPosition(userId, (int)KneeLeft));
        rightDeg = AbductionAngle(km.GetJointPosition(userId, (int)HipRight), km.GetJointPosition(userId, (int)KneeRight));
        return true;
    }

    // 扭腰：軀幹旋轉角度的近似值。真正的軀幹旋轉是肩線相對髖線繞垂直軸轉了
    // 幾度，但 Kinect 骨架給的是關節位置不是可靠的軀幹朝向，這裡改用「髖部
    // 中心偏離肩部中心的橫向距離，除以肩寬」當比例，反正切換算成一個角度感
    // 的數值——這是實作上的近似，不是嚴謹的臨床軀幹旋轉量測，好處是不需要
    // 額外的朝向資料。回傳值有正負號：負＝偏左，正＝偏右，供呼叫端各自累積
    // 左右兩側的峰值角度。
    public static bool TryGetWaistRotationAngle(KinectManager km, long userId, out float signedDeg)
    {
        signedDeg = 0f;
        if (!km.IsJointTracked(userId, (int)ShoulderLeft) || !km.IsJointTracked(userId, (int)ShoulderRight) ||
            !km.IsJointTracked(userId, (int)HipLeft) || !km.IsJointTracked(userId, (int)HipRight))
        {
            return false;
        }

        float sw = ShoulderWidth(km, userId);
        if (sw < 0.05f) return false;

        Vector3 shL = km.GetJointPosition(userId, (int)ShoulderLeft);
        Vector3 shR = km.GetJointPosition(userId, (int)ShoulderRight);
        Vector3 hL = km.GetJointPosition(userId, (int)HipLeft);
        Vector3 hR = km.GetJointPosition(userId, (int)HipRight);

        float shoulderCenterX = (shL.x + shR.x) * 0.5f;
        float hipCenterX = (hL.x + hR.x) * 0.5f;
        float offset = hipCenterX - shoulderCenterX;

        signedDeg = Mathf.Atan2(offset, sw) * Mathf.Rad2Deg;
        return true;
    }

    // 擴胸：肩水平外展角度的近似值。臨床量法是肩外展 90 度、肘彎 90 度時，
    // 手臂能往後展開多少度（正常值約 45 度）。這裡沒有辦法完整重現那個
    // 量測姿勢，改用「手肘超出肩寬的距離／上臂長度」的反正切近似，上臂長
    // 用當下肩到肘的距離即時算（不受身高影響）。
    public static bool TryGetChestExpandAngles(KinectManager km, long userId, out float leftDeg, out float rightDeg)
    {
        leftDeg = rightDeg = 0f;
        if (!km.IsJointTracked(userId, (int)ElbowLeft) || !km.IsJointTracked(userId, (int)ElbowRight) ||
            !km.IsJointTracked(userId, (int)ShoulderLeft) || !km.IsJointTracked(userId, (int)ShoulderRight))
        {
            return false;
        }

        Vector3 shL = km.GetJointPosition(userId, (int)ShoulderLeft);
        Vector3 shR = km.GetJointPosition(userId, (int)ShoulderRight);
        Vector3 eL = km.GetJointPosition(userId, (int)ElbowLeft);
        Vector3 eR = km.GetJointPosition(userId, (int)ElbowRight);

        float shoulderCenterX = (shL.x + shR.x) * 0.5f;
        float halfShoulderWidth = Vector3.Distance(shL, shR) * 0.5f;

        float excessL = Mathf.Max(0f, (shoulderCenterX - eL.x) - halfShoulderWidth);
        float excessR = Mathf.Max(0f, (eR.x - shoulderCenterX) - halfShoulderWidth);
        float upperArmL = Mathf.Max(0.05f, Vector3.Distance(shL, eL));
        float upperArmR = Mathf.Max(0.05f, Vector3.Distance(shR, eR));

        leftDeg = Mathf.Atan2(excessL, upperArmL) * Mathf.Rad2Deg * 2f;
        rightDeg = Mathf.Atan2(excessR, upperArmR) * Mathf.Rad2Deg * 2f;
        return true;
    }

    // 摸膝蓋：回傳左右手到左右膝蓋的原始距離（沿用 IsTouchingKnees 同一套
    // 量測，但不合併、不套門檻），供 WarmupCardController 各自取整段過程的
    // 最小值，換算成 Senior Fitness Test 那種「距離型」達成率跟左右對稱性。
    public static bool TryGetTouchKneesDistances(KinectManager km, long userId, out float distLeft, out float distRight)
    {
        distLeft = distRight = float.MaxValue;
        if (!km.IsJointTracked(userId, (int)HandLeft) || !km.IsJointTracked(userId, (int)HandRight) ||
            !km.IsJointTracked(userId, (int)KneeLeft) || !km.IsJointTracked(userId, (int)KneeRight))
        {
            return false;
        }

        distLeft = Vector3.Distance(km.GetJointPosition(userId, (int)HandLeft), km.GetJointPosition(userId, (int)KneeLeft));
        distRight = Vector3.Distance(km.GetJointPosition(userId, (int)HandRight), km.GetJointPosition(userId, (int)KneeRight));
        return true;
    }
}
