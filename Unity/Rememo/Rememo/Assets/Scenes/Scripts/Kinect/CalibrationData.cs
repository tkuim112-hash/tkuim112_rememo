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

    // 個人化語音音量門檻（KinectSensorSender 反應時間偵測用，見 KinectCalibrationManager
    // ApplyAudioThreshold 說明）。校正沒量到有效底噪基準時維持這個預設值，
    // 跟後端 sensor.py AUDIO_SPEECH_MIN 是同一個數字，兩邊退回值要一致。
    public static float AudioSpeechThreshold = 0.015f;
}