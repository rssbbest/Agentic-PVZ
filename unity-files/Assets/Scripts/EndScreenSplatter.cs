using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;

/// <summary>
/// Looping "someone is throwing eggs and tomatoes at the screen" effect for a
/// Screen Space Overlay canvas. Needs NO art: all sprites (eggs, tomatoes, splats,
/// shell/pulp chunks, drips) are drawn procedurally in cartoon style at startup.
///
/// Put this on a full-screen stretched UI GameObject inside your lose screen.
/// The effect runs while the GameObject is enabled and cleans up when disabled.
/// Uses unscaled time, so it works with Time.timeScale = 0.
/// </summary>
[RequireComponent(typeof(RectTransform))]
public class EndScreenSplatter : MonoBehaviour
{
    [Header("Spawning")]
    public Vector2 spawnInterval = new Vector2(0.4f, 1.0f);
    [Range(0f, 1f)] public float eggChance = 0.5f;
    public int maxSplats = 10;
    public float splatLifetime = 8f;
    public float targetPadding = 120f;

    public enum ThrowOrigin
    {
        ScreenEdges,     // thrown from a random point on the screen border
        DistantPoint,    // thrown from one far-away spot on screen (like a thrower sitting far back)
        RandomOnScreen   // thrown from anywhere on screen
    }

    [Header("Throw origin")]
    public ThrowOrigin origin = ThrowOrigin.DistantPoint;
    [Tooltip("DistantPoint only. Where the thrower is, as a fraction of the screen. (0,0) = bottom-left, (1,1) = top-right, (0.5,0.5) = centre.")]
    public Vector2 distantPoint = new Vector2(0.5f, 0.55f);
    [Tooltip("DistantPoint only. Random scatter around that point, in canvas units. 0 = every throw starts from exactly the same spot.")]
    public float distantSpread = 60f;
    [Tooltip("ScreenEdges only. Positive = starts this far beyond the edge, 0 = exactly on the edge.")]
    public float edgeOffset = 0f;
    [Tooltip("Origin and landing spot are re-rolled if closer than this, so throws always travel a visible distance.")]
    public float minTravelDistance = 350f;

    [Header("Keep text readable (optional)")]
    [Tooltip("Drag your end-screen text here. Most throws will avoid landing on it.")]
    public RectTransform avoidRect;
    [Range(0f, 1f)] public float avoidStrength = 0.85f;

    [Header("Sizes (canvas units)")]
    public float projectileSize = 200f;
    public Vector2 splatSizeRange = new Vector2(340f, 500f);
    public float chunkSize = 46f;

    [Header("Flight (far-away lob)")]
    [Tooltip("Seconds in the air. Longer = you see more of the curve.")]
    public Vector2 flightTime = new Vector2(0.9f, 1.5f);
    [Tooltip("Size when leaving the thrower (1 = Projectile Size). Small = looks far away.")]
    public float startScale = 0.06f;
    [Tooltip("Size at the moment of impact on the glass.")]
    public float endScale = 1.3f;
    [Tooltip("Growth easing. 1 = steady growth, 2+ = stays small longer then swells quickly near the end (like real perspective).")]
    public float growthCurve = 1.8f;
    [Tooltip("Travel easing. 1 = constant speed, 1.3-1.6 = drifts slowly at first then speeds up as it nears you.")]
    public float travelEase = 1.35f;
    [Tooltip("How high the lob rises above the straight line (canvas units). This is the visible curve.")]
    public Vector2 arcHeightRange = new Vector2(180f, 420f);
    [Tooltip("Random sideways bend of the path, so throws don't all curve the same way.")]
    public float sideCurve = 120f;
    public float spin = 540f;

    [Header("Impact feel")]
    public float hitStop = 0.04f;
    [Tooltip("Optional. Any RectTransform to shake on impact (e.g. your text/content root).")]
    public RectTransform shakeTarget;
    public float shakeStrength = 12f;
    public float shakeTime = 0.15f;

    [Header("CRT glitch pulse (optional)")]
    public Material crtMaterial;
    public string glitchProperty = "_Glitch";
    public float glitchPeak = 1f;
    public float glitchBaseline = 0f;

    [Header("Sound (optional)")]
    public AudioClip[] impactSounds;
    [Range(0f, 1f)] public float soundVolume = 0.8f;

    [Header("Quality")]
    [Tooltip("Texture resolution of generated splats. 256 is plenty; 384 is crisper.")]
    public int splatTexSize = 256;

    // ------------------------------------------------------------------

    static readonly Color Outline = new Color(0.20f, 0.09f, 0.10f, 1f);
    static readonly Color Cream = new Color(1f, 0.98f, 0.90f, 1f);
    static readonly Color CreamShade = new Color(0.91f, 0.84f, 0.70f, 1f);
    static readonly Color EggWhite = new Color(1f, 1f, 0.97f, 1f);
    static readonly Color EggWhiteRim = new Color(0.80f, 0.86f, 0.93f, 1f);
    static readonly Color Yolk = new Color(1f, 0.78f, 0.10f, 1f);
    static readonly Color YolkShade = new Color(1f, 0.58f, 0.05f, 1f);
    static readonly Color Red = new Color(0.92f, 0.17f, 0.12f, 1f);
    static readonly Color RedShade = new Color(0.66f, 0.07f, 0.08f, 1f);
    static readonly Color RedLight = new Color(1f, 0.45f, 0.35f, 1f);
    static readonly Color Green = new Color(0.35f, 0.72f, 0.22f, 1f);
    static readonly Color Seed = new Color(1f, 0.94f, 0.62f, 1f);

    RectTransform layer;
    float ppu = 100f;
    readonly List<Texture2D> textures = new List<Texture2D>();
    readonly List<GameObject> splats = new List<GameObject>();
    readonly Vector3[] corners = new Vector3[4];

    Sprite eggProj, tomatoProj, dripSprite;
    Sprite[] eggSplats, tomatoSplats, eggChunks, tomatoChunks;
    AudioSource audioSrc;

    Vector2 shakeOrigin;
    Coroutine shakeRoutine, glitchRoutine;

    class Chunk
    {
        public RectTransform rt;
        public Image img;
        public Vector2 vel;
        public float spin;
    }

    // ------------------------------------------------------------------
    // LIFECYCLE
    // ------------------------------------------------------------------

    void Awake()
    {
        var canvas = GetComponentInParent<Canvas>();
        if (canvas) ppu = canvas.referencePixelsPerUnit;

        var go = new GameObject("SplatLayer", typeof(RectTransform));
        layer = (RectTransform)go.transform;
        layer.SetParent(transform, false);
        layer.anchorMin = Vector2.zero;
        layer.anchorMax = Vector2.one;
        layer.offsetMin = layer.offsetMax = Vector2.zero;
        layer.pivot = new Vector2(0.5f, 0.5f);

        BuildSprites();

        if (impactSounds != null && impactSounds.Length > 0)
        {
            audioSrc = gameObject.AddComponent<AudioSource>();
            audioSrc.playOnAwake = false;
        }
    }

    void OnEnable()
    {
        if (shakeTarget) shakeOrigin = shakeTarget.anchoredPosition;
        StartCoroutine(SpawnLoop());
    }

    void OnDisable()
    {
        StopAllCoroutines();
        shakeRoutine = glitchRoutine = null;
        if (shakeTarget) shakeTarget.anchoredPosition = shakeOrigin;
        if (crtMaterial && crtMaterial.HasProperty(glitchProperty))
            crtMaterial.SetFloat(glitchProperty, glitchBaseline);
        splats.Clear();
        if (layer)
            for (int i = layer.childCount - 1; i >= 0; i--)
                Destroy(layer.GetChild(i).gameObject);
    }

    void OnDestroy()
    {
        foreach (var t in textures) if (t) Destroy(t);
    }

    IEnumerator SpawnLoop()
    {
        while (true)
        {
            yield return new WaitForSecondsRealtime(Random.Range(spawnInterval.x, spawnInterval.y));
            StartCoroutine(Throw(Random.value < eggChance));
        }
    }

    // ------------------------------------------------------------------
    // FLIGHT
    // ------------------------------------------------------------------

    IEnumerator Throw(bool egg)
    {
        Vector2 end = RandomTarget();
        Vector2 start = PickOrigin(end);

        float dur = Random.Range(flightTime.x, flightTime.y);
        float arc = Random.Range(arcHeightRange.x, arcHeightRange.y);
        float side = Random.Range(-1f, 1f) * sideCurve;
        float spinDir = Random.value < 0.5f ? -1f : 1f;
        float spinSpeed = spin * Random.Range(0.6f, 1.2f);

        Vector2 travel = end - start;
        Vector2 perp = travel.sqrMagnitude > 1f
            ? new Vector2(-travel.y, travel.x).normalized
            : Vector2.right;

        Image proj = NewImage("Projectile", layer, egg ? eggProj : tomatoProj);
        RectTransform rt = proj.rectTransform;
        rt.sizeDelta = Vector2.one * projectileSize;

        float sStart = Mathf.Max(0.005f, startScale);
        float ratio = endScale / sStart;

        float t = 0f, rot = Random.Range(0f, 360f), wob = Random.value * 6.28f;
        while (t < 1f)
        {
            t += Time.unscaledDeltaTime / dur;
            float u = Mathf.Clamp01(t);

            // Path: straight line start->end (eased) plus a parabolic lob and a sideways bend.
            // The lob/bend terms are 0 at both ends, so it always leaves the origin and lands exactly on target.
            float bump = 4f * u * (1f - u);
            rt.anchoredPosition = start
                                  + travel * Mathf.Pow(u, travelEase)
                                  + Vector2.up * (arc * bump)
                                  + perp * (side * bump);

            // Size: exponential growth feels like steady approach (like real perspective).
            float s = sStart * Mathf.Pow(ratio, Mathf.Pow(u, growthCurve));
            float w = egg ? 0f : 0.07f * Mathf.Sin(wob + t * 25f); // tomato jiggle
            rt.localScale = new Vector3(s * (1f + w), s * (1f - w), 1f);

            rot += spinSpeed * spinDir * Time.unscaledDeltaTime;
            rt.localRotation = Quaternion.Euler(0, 0, rot);

            // quick fade-in so it doesn't pop into existence
            var c = Color.white; c.a = Mathf.Clamp01(u / 0.08f);
            proj.color = c;

            yield return null;
        }

        StartCoroutine(Impact(end, egg, proj.gameObject));
    }

    Vector2 PickOrigin(Vector2 target)
    {
        Vector2 size = layer.rect.size;
        Vector2 h = size * 0.5f;
        Vector2 o = Vector2.zero;

        for (int i = 0; i < 10; i++)
        {
            switch (origin)
            {
                case ThrowOrigin.ScreenEdges:
                    o = RandomEdgePoint();
                    break;
                case ThrowOrigin.DistantPoint:
                    o = Vector2.Scale(distantPoint - new Vector2(0.5f, 0.5f), size)
                        + Random.insideUnitCircle * distantSpread;
                    break;
                default:
                    o = new Vector2(Random.Range(-h.x, h.x), Random.Range(-h.y, h.y));
                    break;
            }
            if ((o - target).magnitude >= minTravelDistance) break;
        }
        return o;
    }

    // ------------------------------------------------------------------
    // IMPACT
    // ------------------------------------------------------------------

    IEnumerator Impact(Vector2 pos, bool egg, GameObject projectile)
    {
        Shake();
        PulseGlitch();
        PlaySound();

        float size = Random.Range(splatSizeRange.x, splatSizeRange.y);

        var go = new GameObject("Splat", typeof(RectTransform), typeof(CanvasGroup));
        var srt = (RectTransform)go.transform;
        srt.SetParent(layer, false);
        srt.anchorMin = srt.anchorMax = new Vector2(0.5f, 0.5f);
        srt.anchoredPosition = pos;
        srt.sizeDelta = Vector2.zero;
        var group = go.GetComponent<CanvasGroup>();
        group.blocksRaycasts = false;
        group.interactable = false;
        splats.Add(go);
        TrimSplats();

        Image splat = NewImage("SplatImage", srt, Pick(egg ? eggSplats : tomatoSplats));
        var spr = splat.rectTransform;
        spr.sizeDelta = Vector2.one * size;
        spr.localRotation = Quaternion.Euler(0, 0, Random.Range(0f, 360f));
        float flip = Random.value < 0.5f ? -1f : 1f;
        spr.localScale = new Vector3(0f, 0f, 1f);

        // Hit-stop: projectile stays frozen at full size for a moment
        yield return new WaitForSecondsRealtime(hitStop);
        if (projectile) Destroy(projectile);

        StartCoroutine(Chunks(pos, egg));
        StartCoroutine(Drips(srt, egg, size));

        // Pop with overshoot: 0 -> 1.15 -> 1
        float t = 0f, d = 0.18f;
        while (t < 1f && spr)
        {
            t += Time.unscaledDeltaTime / d;
            float u = Mathf.Clamp01(t);
            float s = u < 0.6f ? Mathf.Lerp(0f, 1.15f, u / 0.6f)
                               : Mathf.Lerp(1.15f, 1f, (u - 0.6f) / 0.4f);
            spr.localScale = new Vector3(s * flip, s, 1f);
            yield return null;
        }

        yield return new WaitForSecondsRealtime(splatLifetime);
        if (go) StartCoroutine(FadeAndDestroy(go, group));
    }

    IEnumerator Chunks(Vector2 pos, bool egg)
    {
        Sprite[] pool = egg ? eggChunks : tomatoChunks;
        int n = Random.Range(8, 14);
        var items = new List<Chunk>(n);
        for (int i = 0; i < n; i++)
        {
            Image img = NewImage("Chunk", layer, Pick(pool));
            var rt = img.rectTransform;
            rt.sizeDelta = Vector2.one * chunkSize;
            rt.anchoredPosition = pos;
            rt.localScale = Vector3.one * Random.Range(0.6f, 1.2f);
            float ang = Random.Range(0f, Mathf.PI * 2f);
            float speed = Random.Range(300f, 800f);
            items.Add(new Chunk
            {
                rt = rt,
                img = img,
                vel = new Vector2(Mathf.Cos(ang), Mathf.Sin(ang)) * speed,
                spin = Random.Range(-720f, 720f)
            });
        }

        float life = 0.8f, t = 0f;
        while (t < life)
        {
            float dt = Time.unscaledDeltaTime;
            t += dt;
            float a = 1f - Mathf.Clamp01((t - life * 0.5f) / (life * 0.5f));
            foreach (var it in items)
            {
                if (!it.rt) continue;
                it.vel += Vector2.down * 1800f * dt; // gravity
                it.rt.anchoredPosition += it.vel * dt;
                it.rt.Rotate(0, 0, it.spin * dt);
                it.rt.localScale *= (1f - 0.5f * dt);
                var c = it.img.color; c.a = a; it.img.color = c;
            }
            yield return null;
        }
        foreach (var it in items) if (it.rt) Destroy(it.rt.gameObject);
    }

    IEnumerator Drips(RectTransform parent, bool egg, float splatSize)
    {
        int n = Random.Range(2, 5);
        for (int i = 0; i < n; i++)
        {
            Color tint;
            if (egg) tint = Random.value < 0.3f ? Yolk : EggWhite;
            else tint = Red;
            StartCoroutine(OneDrip(parent, tint, splatSize));
        }
        yield break;
    }

    IEnumerator OneDrip(RectTransform parent, Color tint, float splatSize)
    {
        yield return new WaitForSecondsRealtime(Random.Range(0.1f, 0.5f));
        if (!parent) yield break;

        Image img = NewImage("Drip", parent, dripSprite);
        img.type = Image.Type.Sliced;
        img.color = tint;
        img.transform.SetSiblingIndex(0); // behind the splat so its top edge is hidden
        var rt = img.rectTransform;
        rt.pivot = new Vector2(0.5f, 1f); // grows downward
        rt.anchoredPosition = new Vector2(Random.Range(-0.2f, 0.2f) * splatSize,
                                          Random.Range(-0.18f, 0.05f) * splatSize);
        float w = 48f * Random.Range(0.7f, 1.05f);
        float len = Random.Range(0.25f, 0.7f) * splatSize;
        float dur = Random.Range(2.5f, 4.5f);

        float t = 0f;
        while (t < 1f && img)
        {
            t += Time.unscaledDeltaTime / dur;
            float e = 1f - Mathf.Pow(1f - Mathf.Clamp01(t), 3f); // fast start, slow crawl
            rt.sizeDelta = new Vector2(w, Mathf.Max(64f, len * e));
            yield return null;
        }
    }

    // ------------------------------------------------------------------
    // FEEL
    // ------------------------------------------------------------------

    void Shake()
    {
        if (!shakeTarget) return;
        if (shakeRoutine != null) StopCoroutine(shakeRoutine);
        shakeRoutine = StartCoroutine(ShakeRoutine());
    }

    IEnumerator ShakeRoutine()
    {
        float t = 0f;
        while (t < shakeTime)
        {
            t += Time.unscaledDeltaTime;
            float falloff = 1f - t / shakeTime;
            shakeTarget.anchoredPosition = shakeOrigin + Random.insideUnitCircle * shakeStrength * falloff;
            yield return null;
        }
        shakeTarget.anchoredPosition = shakeOrigin;
    }

    void PulseGlitch()
    {
        if (!crtMaterial || !crtMaterial.HasProperty(glitchProperty)) return;
        if (glitchRoutine != null) StopCoroutine(glitchRoutine);
        glitchRoutine = StartCoroutine(GlitchRoutine());
    }

    IEnumerator GlitchRoutine()
    {
        float t = 0f, d = 0.12f;
        while (t < d)
        {
            t += Time.unscaledDeltaTime;
            crtMaterial.SetFloat(glitchProperty, Mathf.Lerp(glitchPeak, glitchBaseline, t / d));
            yield return null;
        }
        crtMaterial.SetFloat(glitchProperty, glitchBaseline);
    }

    void PlaySound()
    {
        if (!audioSrc || impactSounds == null || impactSounds.Length == 0) return;
        audioSrc.pitch = Random.Range(0.9f, 1.15f);
        audioSrc.PlayOneShot(impactSounds[Random.Range(0, impactSounds.Length)], soundVolume);
    }

    // ------------------------------------------------------------------
    // HELPERS
    // ------------------------------------------------------------------

    void TrimSplats()
    {
        splats.RemoveAll(s => !s);
        while (splats.Count > maxSplats)
        {
            var old = splats[0];
            splats.RemoveAt(0);
            StartCoroutine(FadeAndDestroy(old, old.GetComponent<CanvasGroup>()));
        }
    }

    IEnumerator FadeAndDestroy(GameObject go, CanvasGroup g)
    {
        splats.Remove(go);
        float t = 0f, d = 0.6f;
        while (t < d && go)
        {
            t += Time.unscaledDeltaTime;
            g.alpha = 1f - t / d;
            yield return null;
        }
        if (go) Destroy(go);
    }

    Vector2 RandomEdgePoint()
    {
        Vector2 h = layer.rect.size * 0.5f;
        switch (Random.Range(0, 4))
        {
            case 0: return new Vector2(-h.x - edgeOffset, Random.Range(-h.y, h.y));
            case 1: return new Vector2(h.x + edgeOffset, Random.Range(-h.y, h.y));
            case 2: return new Vector2(Random.Range(-h.x, h.x), h.y + edgeOffset);
            default: return new Vector2(Random.Range(-h.x, h.x), -h.y - edgeOffset);
        }
    }

    Vector2 RandomTarget()
    {
        Vector2 h = layer.rect.size * 0.5f - Vector2.one * targetPadding;
        h = Vector2.Max(h, new Vector2(10f, 10f));
        Vector2 pt = new Vector2(Random.Range(-h.x, h.x), Random.Range(-h.y, h.y));

        if (avoidRect && Random.value < avoidStrength)
        {
            Rect avoid = AvoidLocalRect();
            for (int i = 0; i < 12 && avoid.Contains(pt); i++)
                pt = new Vector2(Random.Range(-h.x, h.x), Random.Range(-h.y, h.y));
        }
        return pt;
    }

    Rect AvoidLocalRect()
    {
        avoidRect.GetWorldCorners(corners);
        Vector2 min = new Vector2(float.MaxValue, float.MaxValue);
        Vector2 max = new Vector2(float.MinValue, float.MinValue);
        for (int i = 0; i < 4; i++)
        {
            Vector2 l = layer.InverseTransformPoint(corners[i]);
            min = Vector2.Min(min, l);
            max = Vector2.Max(max, l);
        }
        return new Rect(min, max - min);
    }

    static Image NewImage(string name, RectTransform parent, Sprite sprite)
    {
        var go = new GameObject(name, typeof(RectTransform), typeof(Image));
        var rt = (RectTransform)go.transform;
        rt.SetParent(parent, false);
        rt.anchorMin = rt.anchorMax = new Vector2(0.5f, 0.5f);
        var img = go.GetComponent<Image>();
        img.sprite = sprite;
        img.raycastTarget = false;
        return img;
    }

    static Sprite Pick(Sprite[] arr) { return arr[Random.Range(0, arr.Length)]; }

    // ------------------------------------------------------------------
    // PROCEDURAL CARTOON ART
    // Everything is drawn from signed distance shapes: flat fill + dark outline
    // + one shade tone + one highlight, matching a cartoon look.
    // ------------------------------------------------------------------

    void BuildSprites()
    {
        eggProj = MakeEggProjectile();
        tomatoProj = MakeTomatoProjectile();

        eggSplats = new Sprite[3];
        tomatoSplats = new Sprite[3];
        for (int i = 0; i < 3; i++)
        {
            eggSplats[i] = MakeSplat(true, Random.Range(0, 100000));
            tomatoSplats[i] = MakeSplat(false, Random.Range(0, 100000));
        }

        eggChunks = new[] { MakeShellA(), MakeShellB(), MakeYolkBit() };
        tomatoChunks = new[] { MakePulpA(), MakePulpB(), MakeSkin(), MakeSeed() };
        dripSprite = MakeDrip();
    }

    // ---- shape helpers ----

    static float Circle(Vector2 p, Vector2 c, float r) { return (p - c).magnitude - r; }

    static float Ellipse(Vector2 p, Vector2 c, Vector2 r, float rot)
    {
        Vector2 q = p - c;
        float cs = Mathf.Cos(-rot), sn = Mathf.Sin(-rot);
        q = new Vector2(q.x * cs - q.y * sn, q.x * sn + q.y * cs);
        return (new Vector2(q.x / r.x, q.y / r.y).magnitude - 1f) * Mathf.Min(r.x, r.y);
    }

    static float Capsule(Vector2 p, Vector2 a, Vector2 b, float r)
    {
        Vector2 pa = p - a, ba = b - a;
        float h = Mathf.Clamp01(Vector2.Dot(pa, ba) / Vector2.Dot(ba, ba));
        return (pa - ba * h).magnitude - r;
    }

    static float SMin(float a, float b, float k)
    {
        float h = Mathf.Clamp01(0.5f + 0.5f * (b - a) / k);
        return Mathf.Lerp(b, a, h) - k * h * (1f - h);
    }

    static System.Func<Vector2, float, Color> Flat(Color c) { return (p, d) => c; }

    static Color[] NewPixels(int w, int h)
    {
        var px = new Color[w * h];
        for (int i = 0; i < px.Length; i++) px[i] = new Color(0, 0, 0, 0);
        return px;
    }

    static Color Over(Color s, Color b)
    {
        float a = s.a + b.a * (1f - s.a);
        if (a <= 0f) return new Color(0, 0, 0, 0);
        Color r = (s * s.a + b * b.a * (1f - s.a)) / a;
        r.a = a;
        return r;
    }

    // Paints one shape layer (fill + outline, antialiased) onto the pixel buffer.
    // bbC/bbR = bounding circle (pixels from texture centre) to keep generation fast.
    static void Paint(Color[] px, int w, int h, Vector2 bbC, float bbR,
                      System.Func<Vector2, float> sdf,
                      System.Func<Vector2, float, Color> fill,
                      Color outline, float ow)
    {
        int x0 = Mathf.Max(0, Mathf.FloorToInt(w * 0.5f + bbC.x - bbR - 2f));
        int x1 = Mathf.Min(w - 1, Mathf.CeilToInt(w * 0.5f + bbC.x + bbR + 2f));
        int y0 = Mathf.Max(0, Mathf.FloorToInt(h * 0.5f + bbC.y - bbR - 2f));
        int y1 = Mathf.Min(h - 1, Mathf.CeilToInt(h * 0.5f + bbC.y + bbR + 2f));

        for (int y = y0; y <= y1; y++)
        {
            for (int x = x0; x <= x1; x++)
            {
                Vector2 p = new Vector2(x + 0.5f - w * 0.5f, y + 0.5f - h * 0.5f);
                float d = sdf(p);
                float aOut = Mathf.Clamp01(0.5f - d);
                if (aOut <= 0f) continue;

                Color c = fill(p, d);
                if (ow > 0f)
                    c = Color.Lerp(outline, c, Mathf.Clamp01(0.5f - (d + ow)));
                c.a = aOut;

                int i = y * w + x;
                px[i] = Over(c, px[i]);
            }
        }
    }

    Sprite MakeSprite(Color[] px, int w, int h, Vector4 border = default(Vector4))
    {
        var tex = new Texture2D(w, h, TextureFormat.RGBA32, false);
        tex.wrapMode = TextureWrapMode.Clamp;
        tex.filterMode = FilterMode.Bilinear;
        tex.SetPixels(px);
        tex.Apply();
        textures.Add(tex);
        return Sprite.Create(tex, new Rect(0, 0, w, h), new Vector2(0.5f, 0.5f), ppu,
                             0, SpriteMeshType.FullRect, border);
    }

    // ---- projectiles ----

    Sprite MakeEggProjectile()
    {
        int S = 128;
        var px = NewPixels(S, S);
        Vector2 c = new Vector2(0, -2);
        float ry = 52f, rx0 = 40f;

        System.Func<Vector2, float> sdf = p =>
        {
            float y = (p.y - c.y) / ry;
            float rx = rx0 * (1f - 0.16f * y); // narrower at the top = egg shape
            float x = (p.x - c.x) / rx;
            return (Mathf.Sqrt(x * x + y * y) - 1f) * rx0;
        };

        Vector2 shadeDir = new Vector2(0.65f, -0.76f);
        Paint(px, S, S, Vector2.zero, S, sdf,
              (p, d) => Vector2.Dot(p, shadeDir) > 22f ? CreamShade : Cream, Outline, 5f);

        Vector2 sc = new Vector2(-16, 22);
        Paint(px, S, S, sc, 16, p => Ellipse(p, sc, new Vector2(7, 12), 0.5f),
              Flat(Color.white), Outline, 0f);

        return MakeSprite(px, S, S);
    }

    Sprite MakeTomatoProjectile()
    {
        int S = 128;
        var px = NewPixels(S, S);
        Vector2 c = new Vector2(0, -5);
        Vector2 radii = new Vector2(54, 47);

        Vector2 shadeDir = new Vector2(0.6f, -0.8f);
        Paint(px, S, S, Vector2.zero, S, p => Ellipse(p, c, radii, 0f),
              (p, d) => Vector2.Dot(p - c, shadeDir) > 30f ? RedShade : Red, Outline, 5f);

        Vector2 ha = new Vector2(-32, 10), hb = new Vector2(-24, 30);
        Paint(px, S, S, (ha + hb) * 0.5f, 25, p => Capsule(p, ha, hb, 4.5f),
              Flat(RedLight), Outline, 0f);

        // green star calyx + stem
        Vector2 cc = new Vector2(0, 36);
        float[] ang = { 190f, 230f, 270f, 310f, 350f };
        float[] len = { 22f, 17f, 13f, 17f, 22f };
        Paint(px, S, S, new Vector2(0, 40), 40, p =>
        {
            float d = Capsule(p, cc, new Vector2(0, 56), 4f);
            for (int i = 0; i < 5; i++)
            {
                float a = ang[i] * Mathf.Deg2Rad;
                Vector2 e = cc + new Vector2(Mathf.Cos(a), Mathf.Sin(a)) * len[i];
                d = Mathf.Min(d, Capsule(p, cc, e, 5.5f));
            }
            return d;
        }, Flat(Green), Outline, 3.5f);

        return MakeSprite(px, S, S);
    }

    // ---- splats ----

    Sprite MakeSplat(bool egg, int seed)
    {
        int S = Mathf.Max(128, splatTexSize);
        float half = S * 0.5f;
        float R = half * 0.52f;
        float ow = S * 0.03f;
        var rng = new System.Random(seed);
        var px = NewPixels(S, S);

        // wobbly body
        float[] amp = new float[5], ph = new float[5];
        for (int i = 0; i < 5; i++)
        {
            amp[i] = (0.02f + (float)rng.NextDouble() * 0.07f) / (1f + i * 0.3f);
            ph[i] = (float)rng.NextDouble() * Mathf.PI * 2f;
        }

        // flying droplets
        int nd = rng.Next(6, 10);
        var dc = new Vector2[nd];
        var dr = new float[nd];
        for (int i = 0; i < nd; i++)
        {
            float a = (float)rng.NextDouble() * Mathf.PI * 2f;
            float dist = R * (1.2f + (float)rng.NextDouble() * 0.45f);
            dc[i] = new Vector2(Mathf.Cos(a), Mathf.Sin(a)) * dist;
            dr[i] = half * (0.035f + (float)rng.NextDouble() * 0.045f);
        }

        // splash fingers
        int na = rng.Next(3, 6);
        var a0 = new Vector2[na];
        var a1 = new Vector2[na];
        var ar = new float[na];
        for (int i = 0; i < na; i++)
        {
            float a = (float)rng.NextDouble() * Mathf.PI * 2f;
            Vector2 dir = new Vector2(Mathf.Cos(a), Mathf.Sin(a));
            a0[i] = dir * R * 0.7f;
            a1[i] = dir * R * (1.25f + (float)rng.NextDouble() * 0.25f);
            ar[i] = R * (0.08f + (float)rng.NextDouble() * 0.06f);
        }

        float k1 = S * 0.03f, k2 = S * 0.04f;
        System.Func<Vector2, float> blob = p =>
        {
            float ang = Mathf.Atan2(p.y, p.x);
            float r = 1f;
            for (int k = 0; k < 5; k++) r += amp[k] * Mathf.Sin((k + 2) * ang + ph[k]);
            float d = p.magnitude - R * r;
            for (int i = 0; i < nd; i++) d = SMin(d, Circle(p, dc[i], dr[i]), k1);
            for (int i = 0; i < na; i++) d = SMin(d, Capsule(p, a0[i], a1[i], ar[i]), k2);
            return d;
        };

        Color baseC = egg ? EggWhite : Red;
        Color rimC = egg ? EggWhiteRim : RedShade;
        float band = S * 0.05f;
        Paint(px, S, S, Vector2.zero, S, blob,
              (p, d) => d > -ow - band ? rimC : baseC, Outline, ow);

        if (egg)
        {
            Vector2 yc = new Vector2(((float)rng.NextDouble() - 0.5f) * R * 0.3f,
                                     ((float)rng.NextDouble() - 0.5f) * R * 0.3f);
            float yr = R * 0.34f;
            Vector2 shadeDir = new Vector2(0.6f, -0.8f);
            Paint(px, S, S, yc, yr * 1.2f,
                  p => Ellipse(p, yc, new Vector2(yr, yr * 0.9f), 0f),
                  (p, d) => Vector2.Dot(p - yc, shadeDir) > yr * 0.4f ? YolkShade : Yolk,
                  Outline, S * 0.022f);
            Vector2 sc = yc + new Vector2(-yr * 0.38f, yr * 0.38f);
            Paint(px, S, S, sc, yr * 0.3f,
                  p => Ellipse(p, sc, new Vector2(yr * 0.22f, yr * 0.13f), 0.7f),
                  Flat(Color.white), Outline, 0f);
        }
        else
        {
            // lighter pulp patches
            for (int i = 0; i < 3; i++)
            {
                Vector2 pc = new Vector2(((float)rng.NextDouble() - 0.5f) * R * 0.8f,
                                         ((float)rng.NextDouble() - 0.5f) * R * 0.8f);
                Vector2 pr = new Vector2(R * (0.18f + (float)rng.NextDouble() * 0.12f),
                                         R * (0.12f + (float)rng.NextDouble() * 0.10f));
                float rot = (float)rng.NextDouble() * 3.14f;
                Paint(px, S, S, pc, Mathf.Max(pr.x, pr.y),
                      p => Ellipse(p, pc, pr, rot),
                      Flat(Color.Lerp(Red, RedLight, 0.6f)), Outline, 0f);
            }
            // seeds
            for (int i = 0; i < 6; i++)
            {
                float a = (float)rng.NextDouble() * Mathf.PI * 2f;
                float dist = R * 0.55f * Mathf.Sqrt((float)rng.NextDouble());
                Vector2 sc = new Vector2(Mathf.Cos(a), Mathf.Sin(a)) * dist;
                Vector2 sr = new Vector2(S * 0.026f, S * 0.015f);
                float rot = (float)rng.NextDouble() * 3.14f;
                Paint(px, S, S, sc, sr.x,
                      p => Ellipse(p, sc, sr, rot),
                      Flat(Seed), new Color(0.55f, 0.08f, 0.08f, 1f), S * 0.007f);
            }
            // shine streak
            Vector2 ha = new Vector2(-0.5f, 0.2f) * R, hb = new Vector2(-0.25f, 0.5f) * R;
            Paint(px, S, S, (ha + hb) * 0.5f, R * 0.5f,
                  p => Capsule(p, ha, hb, S * 0.018f),
                  Flat(new Color(1f, 0.7f, 0.62f, 1f)), Outline, 0f);
        }

        return MakeSprite(px, S, S);
    }

    // ---- chunks ----

    Sprite MakeShellA()
    {
        int S = 64; var px = NewPixels(S, S);
        Paint(px, S, S, Vector2.zero, S,
              p => Mathf.Max(Circle(p, Vector2.zero, 24f), -Circle(p, new Vector2(10, -8), 20f)),
              Flat(Cream), Outline, 4f);
        return MakeSprite(px, S, S);
    }

    Sprite MakeShellB()
    {
        int S = 64; var px = NewPixels(S, S);
        Paint(px, S, S, Vector2.zero, S,
              p => Mathf.Max(Ellipse(p, Vector2.zero, new Vector2(22, 12), 0.45f),
                             -Circle(p, new Vector2(14, 6), 10f)),
              Flat(Cream), Outline, 4f);
        return MakeSprite(px, S, S);
    }

    Sprite MakeYolkBit()
    {
        int S = 64; var px = NewPixels(S, S);
        Paint(px, S, S, Vector2.zero, S, p => Circle(p, Vector2.zero, 18f),
              Flat(Yolk), Outline, 4f);
        Vector2 sc = new Vector2(-6, 6);
        Paint(px, S, S, sc, 8, p => Ellipse(p, sc, new Vector2(4, 2.5f), 0.7f),
              Flat(Color.white), Outline, 0f);
        return MakeSprite(px, S, S);
    }

    Sprite MakePulpA()
    {
        int S = 64; var px = NewPixels(S, S);
        Paint(px, S, S, Vector2.zero, S, p => Ellipse(p, Vector2.zero, new Vector2(24, 17), 0.35f),
              Flat(Red), Outline, 4f);
        Vector2 sc = new Vector2(-8, 4);
        Paint(px, S, S, sc, 8, p => Ellipse(p, sc, new Vector2(5, 2.5f), 0.4f),
              Flat(RedLight), Outline, 0f);
        return MakeSprite(px, S, S);
    }

    Sprite MakePulpB()
    {
        int S = 64; var px = NewPixels(S, S);
        Paint(px, S, S, Vector2.zero, S, p => Circle(p, Vector2.zero, 20f),
              (p, d) => Vector2.Dot(p, new Vector2(0.6f, -0.8f)) > 8f ? RedShade : Red, Outline, 4f);
        return MakeSprite(px, S, S);
    }

    Sprite MakeSkin()
    {
        int S = 64; var px = NewPixels(S, S);
        Paint(px, S, S, Vector2.zero, S,
              p => Capsule(p, new Vector2(-18, -6), new Vector2(18, 8), 8f),
              Flat(RedShade), Outline, 4f);
        return MakeSprite(px, S, S);
    }

    Sprite MakeSeed()
    {
        int S = 64; var px = NewPixels(S, S);
        Paint(px, S, S, Vector2.zero, S, p => Ellipse(p, Vector2.zero, new Vector2(16, 9), -0.4f),
              Flat(Seed), Outline, 3f);
        return MakeSprite(px, S, S);
    }

    // ---- drip (9-sliced: rounded bulb at the bottom, stretchy stem above) ----

    Sprite MakeDrip()
    {
        int W = 48, H = 128;
        var px = NewPixels(W, H);
        float capY = -44f;
        Paint(px, W, H, Vector2.zero, H, p =>
        {
            float stem = Mathf.Max(Mathf.Abs(p.x) - 12f, capY - p.y);
            float cap = Circle(p, new Vector2(0, capY), 18f);
            return SMin(stem, cap, 10f);
        }, Flat(Color.white), new Color(0.25f, 0.25f, 0.25f, 1f), 4f);
        // border = (left, bottom, right, top)
        return MakeSprite(px, W, H, new Vector4(0, 56, 0, 4));
    }
}