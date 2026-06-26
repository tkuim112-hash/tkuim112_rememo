using UnityEngine;
using UnityEngine.UI;
using System.Collections.Generic;

public class SkeletonRenderer : MonoBehaviour
{
    [Tooltip("顯示骨架的 RawImage")]
    public RawImage skeletonImage;

    private KinectManager kinectManager;
    private Texture2D skeletonTexture;
    private Color32[] pixels;

    private const int TEX_W = 512;
    private const int TEX_H = 424;

    private static readonly (KinectInterop.JointType, KinectInterop.JointType)[] bones =
    {
        // 脊椎
        (KinectInterop.JointType.Head,          KinectInterop.JointType.Neck),
        (KinectInterop.JointType.Neck,          KinectInterop.JointType.SpineShoulder),
        (KinectInterop.JointType.SpineShoulder, KinectInterop.JointType.SpineMid),
        (KinectInterop.JointType.SpineMid,      KinectInterop.JointType.SpineBase),
        // 左臂
        (KinectInterop.JointType.SpineShoulder, KinectInterop.JointType.ShoulderLeft),
        (KinectInterop.JointType.ShoulderLeft,  KinectInterop.JointType.ElbowLeft),
        (KinectInterop.JointType.ElbowLeft,     KinectInterop.JointType.WristLeft),
        (KinectInterop.JointType.WristLeft,     KinectInterop.JointType.HandLeft),
        // 右臂
        (KinectInterop.JointType.SpineShoulder, KinectInterop.JointType.ShoulderRight),
        (KinectInterop.JointType.ShoulderRight, KinectInterop.JointType.ElbowRight),
        (KinectInterop.JointType.ElbowRight,    KinectInterop.JointType.WristRight),
        (KinectInterop.JointType.WristRight,    KinectInterop.JointType.HandRight),
        // 髖部與腿部（坐姿時追蹤不到就自然不畫）
        (KinectInterop.JointType.SpineBase,     KinectInterop.JointType.HipLeft),
        (KinectInterop.JointType.HipLeft,       KinectInterop.JointType.KneeLeft),
        (KinectInterop.JointType.KneeLeft,      KinectInterop.JointType.AnkleLeft),
        (KinectInterop.JointType.AnkleLeft,     KinectInterop.JointType.FootLeft),
        (KinectInterop.JointType.SpineBase,     KinectInterop.JointType.HipRight),
        (KinectInterop.JointType.HipRight,      KinectInterop.JointType.KneeRight),
        (KinectInterop.JointType.KneeRight,     KinectInterop.JointType.AnkleRight),
        (KinectInterop.JointType.AnkleRight,    KinectInterop.JointType.FootRight),
    };

    private static readonly HashSet<KinectInterop.JointType> inferredAllowed = new HashSet<KinectInterop.JointType>
{
    KinectInterop.JointType.ShoulderLeft,
    KinectInterop.JointType.ShoulderRight,
    KinectInterop.JointType.SpineMid,      // 視情況加，坐姿容易 Inferred
};

    private bool IsUsableJoint(long userId, int jointIndex)
    {
        var state = kinectManager.GetJointTrackingState(userId, jointIndex);
        if (state == KinectInterop.TrackingState.Tracked) return true;
        if (state == KinectInterop.TrackingState.Inferred)
            return inferredAllowed.Contains((KinectInterop.JointType)jointIndex);
        return false;
    }

    void Start()
    {
        kinectManager = KinectManager.Instance;
        skeletonTexture = new Texture2D(TEX_W, TEX_H, TextureFormat.RGBA32, false);
        pixels = new Color32[TEX_W * TEX_H];

        if (skeletonImage != null)
            skeletonImage.texture = skeletonTexture;
        else
            Debug.LogError("[SkeletonRenderer] skeletonImage 未指定，請在 Inspector 設定 RawImage");
    }

    void Update()
    {
        if (kinectManager == null || !kinectManager.IsInitialized()) return;

        // 清空為黑色背景
        for (int i = 0; i < pixels.Length; i++)
            pixels[i] = new Color32(0, 0, 0, 255);

        long userId = kinectManager.GetPrimaryUserID();
        if (userId != 0)
        {
            // 畫連線
            foreach (var (jointA, jointB) in bones)
            {
                if (IsUsableJoint(userId, (int)jointA) && IsUsableJoint(userId, (int)jointB))
                {
                    Vector2 posA = WorldToPixel(kinectManager.GetJointPosition(userId, (int)jointA));
                    Vector2 posB = WorldToPixel(kinectManager.GetJointPosition(userId, (int)jointB));
                    DrawLine(pixels, (int)posA.x, (int)posA.y, (int)posB.x, (int)posB.y,
                             new Color32(0, 180, 255, 255));
                }
            }

            // 畫關節點
            for (int j = 0; j < 25; j++)
            {
                if (IsUsableJoint(userId, j))
                {
                    Vector2 pos = WorldToPixel(kinectManager.GetJointPosition(userId, j));
                    int radius = (j == (int)KinectInterop.JointType.Head) ? 8 : 5;
                    DrawCircle(pixels, (int)pos.x, (int)pos.y, radius, new Color32(255, 220, 0, 255));
                }
            }
        }

        skeletonTexture.SetPixels32(pixels);
        skeletonTexture.Apply();
    }

    private Vector2 WorldToPixel(Vector3 worldPos)
    {
        float x = (worldPos.x / 0.8f * 0.5f + 0.5f) * TEX_W;
        float y = ((worldPos.y - 0.4f) / 1.2f) * TEX_H;

        return new Vector2(
            Mathf.Clamp(x, 0, TEX_W - 1),
            Mathf.Clamp(y, 0, TEX_H - 1)
        );
    }


    /// Bresenham 畫線演算法
    private void DrawLine(Color32[] px, int x0, int y0, int x1, int y1, Color32 color)
    {
        int dx = Mathf.Abs(x1 - x0), dy = Mathf.Abs(y1 - y0);
        int sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
        int err = dx - dy;
        while (true)
        {
            SetPixel(px, x0, y0, color);
            if (x0 == x1 && y0 == y1) break;
            int e2 = 2 * err;
            if (e2 > -dy) { err -= dy; x0 += sx; }
            if (e2 < dx) { err += dx; y0 += sy; }
        }
    }

    private void DrawCircle(Color32[] px, int cx, int cy, int r, Color32 color)
    {
        for (int y = -r; y <= r; y++)
            for (int x = -r; x <= r; x++)
                if (x * x + y * y <= r * r)
                    SetPixel(px, cx + x, cy + y, color);
    }

    private void SetPixel(Color32[] px, int x, int y, Color32 color)
    {
        if (x >= 0 && x < TEX_W && y >= 0 && y < TEX_H)
            px[y * TEX_W + x] = color;
    }
}