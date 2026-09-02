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

    static float ShoulderWidth(KinectManager km, long userId)
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

    public static bool IsTouchingKnees(KinectManager km, long userId)
    {
        if (!km.IsJointTracked(userId, (int)HandLeft) || !km.IsJointTracked(userId, (int)HandRight) ||
            !km.IsJointTracked(userId, (int)KneeLeft) || !km.IsJointTracked(userId, (int)KneeRight))
        {
            return false;
        }

        float sw = ShoulderWidth(km, userId);
        if (sw < 0.05f) return false;

        Vector3 hL = km.GetJointPosition(userId, (int)HandLeft);
        Vector3 hR = km.GetJointPosition(userId, (int)HandRight);
        Vector3 kL = km.GetJointPosition(userId, (int)KneeLeft);
        Vector3 kR = km.GetJointPosition(userId, (int)KneeRight);

        // 實測發現手自然垂放（站好不動、沒有伸手）距離膝蓋就已經只有肩寬的
        // 0.29~0.40 倍，原本 1.6 倍的門檻太寬鬆，站著不動就會被判定成「碰到」。
        // 改到 0.9 倍又太緊——彎腰時 Kinect 對肩膀的追蹤準確度會下降，量到的
        // 肩寬（當比例基準）本身就會飄動，伸手伸到底也量不到那麼低的比例。抓在
        // 兩次實測中間，改成 1.2 倍。
        float distL = Vector3.Distance(hL, kL);
        float distR = Vector3.Distance(hR, kR);
        bool touching = distL < sw * 1.2f && distR < sw * 1.2f;

        // 除錯用：確認過原因了記得把這段 log 拿掉。
        if (Time.frameCount % 30 == 0)
        {
            Debug.Log($"[TouchKnees] distL={distL:F3} distR={distR:F3} sw={sw:F3} threshold={sw * 1.2f:F3} touching={touching}");
        }

        return touching;
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
}
