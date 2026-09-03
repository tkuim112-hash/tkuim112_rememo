using UnityEngine;

// AvatarController 原本用「複製 Kinect 關節朝向」驅動大腿/小腿，但髖關節的
// 朝向資料本身就不準（AvatarController.cs 裡 IsLegJoint 那段官方原始碼，刻意
// 讓髖、膝關節就算拿到無意義資料也照樣套用，這是抬腿動作常常沒反應的根源）。
//
// 這支腳本改用 KinectTwoBoneIK：只依賴 Kinect 給的「關節位置」（比朝向準
// 很多），把腳掌直接「放」到 Kinect 抓到的位置。髖、膝這兩根骨頭改由這支
// 腳本接管，AvatarController 不再驅動它們，避免兩邊打架。
[RequireComponent(typeof(AvatarController))]
public class KinectLegIK : MonoBehaviour
{
    AvatarController avatarController;

    Transform hipLeft, kneeLeft, footLeft;
    Transform hipRight, kneeRight, footRight;

    float upperLegLenLeft, lowerLegLenLeft;
    float upperLegLenRight, lowerLegLenRight;

    // Kinect 偶爾會誤判關節位置、單幀瞬間跳到很遠的地方（實測看過 0.4~0.5 公尺
    // 的瞬間跳動），這種雜訊幀直接餵給 IK 會讓腿瞬間折疊變形。用「上一幀合理
    // 位置」擋一下：這一幀若跳動超過合理速度，視為雜訊，沿用上一次的位置。
    const float maxFootSpeed = 3f; // 公尺/秒，比長者實際動作速度寬鬆很多
    Vector3 lastTargetLeft, lastTargetRight;
    bool hasLastTargetLeft, hasLastTargetRight;

    // 依 mirroredMovement 決定：驅動「左腳骨頭」的，究竟該用 Kinect 的
    // FootLeft/KneeLeft 還是 FootRight/KneeRight（要跟 AvatarController
    // 其他部位的鏡射方向一致）。
    KinectInterop.JointType hipJointLeft, footJointLeft, kneeJointLeft;
    KinectInterop.JointType hipJointRight, footJointRight, kneeJointRight;

    // 角色是小孩比例的模型，腿全長比真人短很多；Kinect 給的是真人世界座標的
    // 目標位置，直接套用會逼著短腿硬凹去搆一個「以真人比例來看很近、但以角色
    // 比例來看太遠」的目標，腿才會摺成怪姿勢。用「角色腿長 / 這個人當下的真實
    // 腿長」算一個縮放比例，把目標點相對髖部的偏移量按比例縮小，角色維持自己
    // 的小孩外型，只是動作幅度照比例對應。
    float legScaleLeft = 1f, legScaleRight = 1f;

    void Start()
    {
        avatarController = GetComponent<AvatarController>();

        hipLeft = GetBone(KinectInterop.JointType.HipLeft);
        kneeLeft = GetBone(KinectInterop.JointType.KneeLeft);
        // AvatarController 的 jointMap2boneIndex 把 FootLeft/FootRight 對應到
        // LeftToes/RightToes，但這個外掛沒有啟用腳趾骨頭的對照（見
        // boneIndex2MecanimMap 裡被註解掉的那兩行），查 FootLeft 一定拿 null。
        // 角色骨架上真正的「腳」骨頭要用 AnkleLeft/AnkleRight 去查，
        // AvatarController.Awake() 初始化 leftFoot/rightFoot 時也是這樣退回的。
        footLeft = GetBone(KinectInterop.JointType.AnkleLeft);

        hipRight = GetBone(KinectInterop.JointType.HipRight);
        kneeRight = GetBone(KinectInterop.JointType.KneeRight);
        footRight = GetBone(KinectInterop.JointType.AnkleRight);

        if (hipLeft == null || kneeLeft == null || footLeft == null ||
            hipRight == null || kneeRight == null || footRight == null)
        {
            Debug.LogError("[KinectLegIK] 找不到腿部骨頭，這支腳本無法運作。");
            enabled = false;
            return;
        }

        upperLegLenLeft = Vector3.Distance(hipLeft.position, kneeLeft.position);
        lowerLegLenLeft = Vector3.Distance(kneeLeft.position, footLeft.position);
        upperLegLenRight = Vector3.Distance(hipRight.position, kneeRight.position);
        lowerLegLenRight = Vector3.Distance(kneeRight.position, footRight.position);

        bool mirrored = avatarController.mirroredMovement;
        hipJointLeft = mirrored ? KinectInterop.JointType.HipRight : KinectInterop.JointType.HipLeft;
        footJointLeft = mirrored ? KinectInterop.JointType.FootRight : KinectInterop.JointType.FootLeft;
        kneeJointLeft = mirrored ? KinectInterop.JointType.KneeRight : KinectInterop.JointType.KneeLeft;
        hipJointRight = mirrored ? KinectInterop.JointType.HipLeft : KinectInterop.JointType.HipRight;
        footJointRight = mirrored ? KinectInterop.JointType.FootLeft : KinectInterop.JointType.FootRight;
        kneeJointRight = mirrored ? KinectInterop.JointType.KneeLeft : KinectInterop.JointType.KneeRight;

        // 髖、膝這幾根骨頭改由這支腳本用 IK 驅動，AvatarController 不要再用它
        // 原本那套朝向複製邏輯去動它們。
        DisableBone(KinectInterop.JointType.HipLeft);
        DisableBone(KinectInterop.JointType.KneeLeft);
        DisableBone(KinectInterop.JointType.HipRight);
        DisableBone(KinectInterop.JointType.KneeRight);
    }

    Transform GetBone(KinectInterop.JointType joint)
    {
        int boneIndex = avatarController.GetBoneIndexByJoint(joint, false);
        return avatarController.GetBoneTransform(boneIndex);
    }

    void DisableBone(KinectInterop.JointType joint)
    {
        int boneIndex = avatarController.GetBoneIndexByJoint(joint, false);
        if (boneIndex >= 0)
        {
            avatarController.DisableBone(boneIndex);
        }
    }

    // 排在 AvatarController.UpdateAvatar()（在 KinectManager.Update() 裡跑）之後，
    // 確保這邊算出來的腿部姿勢不會被蓋掉。
    void LateUpdate()
    {
        if (!KinectPoseChecker.TryGetTrackedUser(out var km, out var userId)) return;

        SolveLeg(km, userId, hipJointLeft, footJointLeft, kneeJointLeft, hipLeft, kneeLeft, footLeft, upperLegLenLeft, lowerLegLenLeft,
            ref lastTargetLeft, ref hasLastTargetLeft, ref legScaleLeft);
        SolveLeg(km, userId, hipJointRight, footJointRight, kneeJointRight, hipRight, kneeRight, footRight, upperLegLenRight, lowerLegLenRight,
            ref lastTargetRight, ref hasLastTargetRight, ref legScaleRight);
    }

    void SolveLeg(KinectManager km, long userId, KinectInterop.JointType hipJoint, KinectInterop.JointType footJoint, KinectInterop.JointType kneeJoint,
        Transform hip, Transform knee, Transform foot, float upperLen, float lowerLen,
        ref Vector3 lastTarget, ref bool hasLastTarget, ref float legScale)
    {
        if (!km.IsJointTracked(userId, (int)footJoint) || !km.IsJointTracked(userId, (int)hipJoint)) return;

        // 縮放的基準點一定要跟 KinectTwoBoneIK.Solve() 內部三角測量用的 rootPos
        // 是同一個點（也就是 hip.position 這根角色骨頭自己的位置），不能用另外
        // 重新從 Kinect 原始資料算出來的髖部世界座標——兩個「髖部」座標來源不
        // 同，不保證重合，參考系對不起來就會算出離譜的目標位置。
        Vector3 hipWorldPos = hip.position;
        Vector3 rawTargetPos = avatarController.GetJointWorldPos(footJoint);

        // 角色是小孩比例，腿比真人短很多，直接套用真人世界座標會逼短腿硬凹去搆
        // 一個相對過遠的目標。用「這個人當下的真實腿長」跟「角色自己的腿長」算
        // 縮放比例，把目標點相對髖部的偏移量按比例縮小，角色維持小孩外型，只是
        // 動作幅度照比例對應。真實腿長用 Kinect 原始關節距離算，可能有雜訊，所
        // 以縮放比例本身也做平滑，不要一幀變化太大。
        if (km.IsJointTracked(userId, (int)kneeJoint))
        {
            Vector3 kinectHip = km.GetJointPosition(userId, (int)hipJoint);
            Vector3 kinectKnee = km.GetJointPosition(userId, (int)kneeJoint);
            Vector3 kinectFoot = km.GetJointPosition(userId, (int)footJoint);
            float realLegLen = Vector3.Distance(kinectHip, kinectKnee) + Vector3.Distance(kinectKnee, kinectFoot);

            if (realLegLen > 0.1f)
            {
                float rawScale = Mathf.Clamp((upperLen + lowerLen) / realLegLen, 0.2f, 2.5f);
                legScale = Mathf.MoveTowards(legScale, rawScale, 2f * Time.deltaTime);
            }
        }

        Vector3 scaledTargetPos = hipWorldPos + (rawTargetPos - hipWorldPos) * legScale;

        // 用「限制每幀最大移動速度」取代整幀丟棄：單幀雜訊只會被削掉尖峰、下一幀
        // 就會自然修正；就算一開始骨架初始位置跟 Kinect 實際位置差很多（不是雜
        // 訊，是正常現象），也會在幾幀內逐漸追上，不會像整幀丟棄那樣卡死在原地
        // 出不來。
        Vector3 targetPos = hasLastTarget
            ? Vector3.MoveTowards(lastTarget, scaledTargetPos, maxFootSpeed * Time.deltaTime)
            : scaledTargetPos;
        lastTarget = targetPos;
        hasLastTarget = true;

        Vector3 kneeHintPos = km.IsJointTracked(userId, (int)kneeJoint)
            ? avatarController.GetJointWorldPos(kneeJoint)
            : knee.position;

        KinectTwoBoneIK.Solve(hip, knee, foot, targetPos, kneeHintPos, upperLen, lowerLen);
    }
}
