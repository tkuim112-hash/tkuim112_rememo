using UnityEngine;
using UnityEngine.UI;

public class MicVisualizer : MonoBehaviour
{
    public RawImage waveformDisplay;
    public int textureWidth = 1600;
    public int textureHeight = 80;
    public Color waveColor = new Color(0.2f, 0.4f, 0.8f, 1f);
    public float amplify = 10f;

    private AudioClip micClip;
    private Texture2D waveTexture;
    private float[] samples;

    void Start()
    {
        waveTexture = new Texture2D(textureWidth, textureHeight);
        waveTexture.filterMode = FilterMode.Point;
        if (waveformDisplay != null)
            waveformDisplay.texture = waveTexture;

        samples = new float[textureWidth];

        if (Microphone.devices.Length > 0)
        {
            micClip = Microphone.Start(null, true, 1, 44100);
            Debug.Log("[MicVisualizer] 使用麥克風: " + Microphone.devices[0]);
        }
    }

    void Update()
    {
        if (micClip == null) return;

        int pos = Microphone.GetPosition(null);
        if (pos <= 0) return;

        micClip.GetData(samples, 0);

        Color[] pixels = new Color[textureWidth * textureHeight];
        for (int i = 0; i < pixels.Length; i++)
            pixels[i] = Color.clear;

        for (int x = 0; x < textureWidth; x++)
        {
            float sample = samples[x];
            int centerY = Mathf.RoundToInt(textureHeight / 2.35f);
            int waveHeight = Mathf.RoundToInt(sample * textureHeight * amplify);
            int startY = Mathf.Clamp(centerY - Mathf.Abs(waveHeight), 0, textureHeight - 1);
            int endY = Mathf.Clamp(centerY + Mathf.Abs(waveHeight), 0, textureHeight - 1);
            for (int y = startY; y <= endY; y++)
                pixels[y * textureWidth + x] = waveColor;
        }

        waveTexture.SetPixels(pixels);
        waveTexture.Apply();
    }

    void OnDestroy()
    {
        Microphone.End(null);
    }
}