/// <summary>
/// 跨場景共享的校正資料，不需掛在任何物件上。
/// </summary>
public static class CalibrationData
{
    public static bool IsCalibrated = false;

    public static float WorldYMin = -0.5f;
    public static float WorldYMax = 0.5f;
    public static float WorldXMin = -0.5f;
    public static float WorldXMax = 0.5f;
    public static float WorldZ = 1.5f;
}