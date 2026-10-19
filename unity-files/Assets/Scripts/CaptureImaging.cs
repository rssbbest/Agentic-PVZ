using UnityEngine;

// NEW: screenshot resize helpers (in memory only, WebGL can't write files)
public static class CaptureImaging
{
    // NEW: aspect-preserving size, never upscales; maxWidth <= 0 = no resize
    public static Vector2Int DownscaleSize(int width, int height, int maxWidth)
    {
        if (maxWidth <= 0 || width <= 0 || height <= 0 || width <= maxWidth)
        {
            return new Vector2Int(width, height);
        }

        float scale = (float)maxWidth / (float)width;
        int newHeight = Mathf.RoundToInt(height * scale);
        if (newHeight < 1)
        {
            newHeight = 1;
        }
        return new Vector2Int(maxWidth, newHeight);
    }

    // NEW: GPU downscale; returns source itself when no resize is needed
    public static Texture2D Downscale(Texture2D source, int maxWidth)
    {
        if (source == null)
        {
            return null;
        }

        Vector2Int size = DownscaleSize(source.width, source.height, maxWidth);
        if (size.x == source.width && size.y == source.height)
        {
            return source;
        }

        RenderTexture temp = RenderTexture.GetTemporary(size.x, size.y, 0);
        RenderTexture previous = RenderTexture.active;
        Texture2D scaled = null;
        try
        {
            Graphics.Blit(source, temp);
            RenderTexture.active = temp;

            scaled = new Texture2D(size.x, size.y, TextureFormat.RGB24, false);
            scaled.ReadPixels(new Rect(0, 0, size.x, size.y), 0, 0);
            scaled.Apply();
        }
        finally
        {
            // always restore and release, even if the blit fails
            RenderTexture.active = previous;
            RenderTexture.ReleaseTemporary(temp);
        }
        return scaled;
    }
}
