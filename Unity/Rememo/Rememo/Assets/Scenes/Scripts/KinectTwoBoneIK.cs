using UnityEngine;

// 共用的兩節骨頭（例如：髖-膝-腳、肩-肘-手）反向動力學（IK）解算工具，
// KinectLegIK、KinectArmIK 都是靠這個算角度。只依賴 Kinect 給的「關節位置」，
// 不依賴容易失準的「關節朝向」。
//
// 這段三角函數解算沒有實機測試過，效果需要現場驗證。
public static class KinectTwoBoneIK
{
    public static void Solve(Transform root, Transform mid, Transform end,
        Vector3 targetPos, Vector3 midHintPos, float upperLen, float lowerLen)
    {
        Vector3 rootPos = root.position;

        Vector3 toTarget = targetPos - rootPos;
        float targetDist = Mathf.Clamp(toTarget.magnitude, 0.01f, upperLen + lowerLen - 0.001f);
        Vector3 dirToTarget = toTarget.normalized;

        // 餘弦定理：算出 root 到「彎曲後 mid 位置」該轉幾度
        float cosRootAngle = (upperLen * upperLen + targetDist * targetDist - lowerLen * lowerLen) / (2f * upperLen * targetDist);
        float rootAngleDeg = Mathf.Acos(Mathf.Clamp(cosRootAngle, -1f, 1f)) * Mathf.Rad2Deg;

        // mid（膝蓋/手肘）要往哪個方向彎，用 Kinect 抓到的 mid 位置當參考
        Vector3 hintDir = (midHintPos - rootPos).normalized;
        Vector3 bendAxis = Vector3.Cross(dirToTarget, hintDir);
        if (bendAxis.sqrMagnitude < 0.0001f)
        {
            bendAxis = Vector3.Cross(dirToTarget, root.right);
        }
        bendAxis.Normalize();

        Vector3 midDir = Quaternion.AngleAxis(rootAngleDeg, bendAxis) * dirToTarget;
        Vector3 newMidPos = rootPos + midDir * upperLen;

        Vector3 oldMidDir = mid.position - rootPos;
        root.rotation = Quaternion.FromToRotation(oldMidDir, newMidPos - rootPos) * root.rotation;

        Vector3 oldEndDir = end.position - mid.position;
        mid.rotation = Quaternion.FromToRotation(oldEndDir, targetPos - newMidPos) * mid.rotation;

        // 除錯用：確認過原因了記得拿掉。每 30 幀印一次這一節 IK 解算的關鍵數值。
        if (Time.frameCount % 30 == 0)
        {
            Debug.Log($"[TwoBoneIK:{root.name}] rootPos={rootPos:F2} targetPos={targetPos:F2} targetDist={targetDist:F3} " +
                $"upperLen={upperLen:F3} lowerLen={lowerLen:F3} rootAngleDeg={rootAngleDeg:F1} bendAxis={bendAxis:F2} " +
                $"hintDir={hintDir:F2} newMidPos={newMidPos:F2}");
        }
    }
}
