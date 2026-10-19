using UnityEngine;
using TMPro;

[RequireComponent(typeof(TMP_Text))]
public class GigglyText : MonoBehaviour
{
    [System.Serializable]
    public class WobbleSettings
    {
        public bool enabled = true;
        [Tooltip("How far letters move, in text units")]
        public float amplitude = 5f;
        [Tooltip("How fast the wobble cycles")]
        public float speed = 6f;
        [Tooltip("Phase shift between neighboring letters (creates the wave)")]
        public float letterOffset = 0.5f;
        [Tooltip("Starting phase, useful to desync from the other axis")]
        public float phase = 0f;
    }

    [Header("Vertical Wobble")]
    public WobbleSettings vertical = new WobbleSettings
    {
        amplitude = 5f,
        speed = 6f,
        letterOffset = 0.5f
    };

    [Header("Horizontal Wobble")]
    public WobbleSettings horizontal = new WobbleSettings
    {
        amplitude = 3f,
        speed = 4f,
        letterOffset = 0.8f,
        phase = 1.5f
    };

    TMP_Text tmp;

    void Awake() => tmp = GetComponent<TMP_Text>();

    void Update()
    {
        tmp.ForceMeshUpdate();
        var textInfo = tmp.textInfo;

        for (int i = 0; i < textInfo.characterCount; i++)
        {
            var c = textInfo.characterInfo[i];
            if (!c.isVisible) continue;

            int mi = c.materialReferenceIndex;
            int vi = c.vertexIndex;
            var verts = textInfo.meshInfo[mi].vertices;

            float x = horizontal.enabled
                ? Mathf.Sin(Time.time * horizontal.speed + horizontal.phase + i * horizontal.letterOffset) * horizontal.amplitude
                : 0f;

            float y = vertical.enabled
                ? Mathf.Sin(Time.time * vertical.speed + vertical.phase + i * vertical.letterOffset) * vertical.amplitude
                : 0f;

            Vector3 offset = new Vector3(x, y, 0);

            for (int j = 0; j < 4; j++)
                verts[vi + j] += offset;
        }

        for (int i = 0; i < textInfo.meshInfo.Length; i++)
        {
            textInfo.meshInfo[i].mesh.vertices = textInfo.meshInfo[i].vertices;
            tmp.UpdateGeometry(textInfo.meshInfo[i].mesh, i);
        }
    }
}