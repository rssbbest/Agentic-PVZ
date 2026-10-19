using System.Collections.Generic;
using TMPro;
using UnityEngine;

/// <summary>
/// Cartoon laugh emote for UI. Pops "Ha" words (TextMeshPro) out of this
/// RectTransform, lets them float away with a bouncy wobble, then fades them out.
///
/// Setup: add an empty UI object under your character, move it onto the mouth,
/// and add this component. Laughs start automatically (Play On Enable) or can be
/// triggered with Play() from code, a Button's OnClick, or an Animation Event.
/// EmitWord() spits out a single word, handy for syncing with mouth frames.
///
/// Words are pooled, so there are no allocations while laughing.
/// </summary>
[DisallowMultipleComponent]
[RequireComponent(typeof(RectTransform))]
[AddComponentMenu("UI/Effects/Emote Laugh")]
public class EmoteLaugh : MonoBehaviour
{
    public enum Facing { Right, Left }
    public enum WordOrder { Random, Sequential }
    public enum ColorMode { Single, RandomFromPalette, CyclePalette, RandomFromGradient }

    // ------------------------------------------------------------------
    // INSPECTOR
    // ------------------------------------------------------------------

    [Header("Playback")]
    [Tooltip("Start laughing automatically when this object is enabled.")]
    public bool playOnEnable = true;
    [Tooltip("Laugh again after each laugh ends (waits 'Loop Delay' in between).")]
    public bool loop = true;
    [Tooltip("Seconds to wait before the first word when Play() is called.")]
    [Min(0f)] public float startDelay = 0f;
    [Tooltip("How many words come out per laugh (min, max).")]
    public Vector2Int wordsPerLaugh = new Vector2Int(3, 5);
    [Tooltip("Seconds between words inside one laugh (min, max).")]
    public Vector2 wordInterval = new Vector2(0.12f, 0.2f);
    [Tooltip("Pause after the last word before the next laugh starts (min, max). Only used when Loop is on.")]
    public Vector2 loopDelay = new Vector2(0.8f, 1.4f);
    [Tooltip("Ignore Time.timeScale, so the effect still runs while the game is paused.")]
    public bool useUnscaledTime = true;

    [Header("Text")]
    [Tooltip("Words to emit. Rich text tags work too, e.g. <i>Ha</i>.")]
    public string[] words = { "Ha", "Ha", "HA" };
    [Tooltip("Random: any word each time. Sequential: goes through the list in order, starting over each laugh.")]
    public WordOrder wordOrder = WordOrder.Random;
    [Tooltip("TextMeshPro font asset (SDF). Leave empty to use the TMP default font.")]
    public TMP_FontAsset font;
    public FontStyles fontStyle = FontStyles.Bold;
    [Tooltip("Extra spacing between letters.")]
    public float characterSpacing = 0f;

    [Header("Size")]
    [Tooltip("Overall size of every word. Quickest way to make all the text smaller or bigger. " +
             "Only affects the letters, not how far the words fly.")]
    [Range(0.05f, 3f)] public float sizeMultiplier = 1f;
    [Tooltip("Smallest font size a word can get. Each word picks a random size between Min and Max.")]
    [Range(1f, 200f)] public float minFontSize = 36f;
    [Tooltip("Largest font size a word can get. Set it equal to Min Font Size to make every word the same size.")]
    [Range(1f, 200f)] public float maxFontSize = 48f;
    [Tooltip("Keep text size and motion distances in canvas units, no matter how the character or its parents are scaled. " +
             "Keep this on for rigged characters that are scaled up a lot inside the Canvas, otherwise the text is scaled up with them.")]
    public bool ignoreParentScale = true;

    [Header("Color")]
    [Tooltip("Single: always Text Color.\nRandom From Palette: random pick from Palette.\nCycle Palette: Palette colors in order.\nRandom From Gradient: random point on Gradient.")]
    public ColorMode colorMode = ColorMode.Single;
    public Color textColor = new Color(1f, 0.85f, 0.2f, 1f);
    [Tooltip("Used by 'Random From Palette' and 'Cycle Palette'.")]
    public Color[] palette =
    {
        new Color(1f, 0.85f, 0.2f, 1f),   // yellow
        new Color(1f, 0.55f, 0.15f, 1f),  // orange
        new Color(1f, 0.4f, 0.65f, 1f),   // pink
        new Color(0.35f, 0.8f, 1f, 1f),   // sky blue
        new Color(0.55f, 0.9f, 0.3f, 1f), // lime
    };
    [Tooltip("Used by 'Random From Gradient'.")]
    public Gradient gradient = new Gradient();

    [Header("Outline")]
    [Tooltip("Cartoon outline around the letters.")]
    public bool outline = true;
    public Color outlineColor = new Color(0.16f, 0.08f, 0.06f, 1f);
    [Tooltip("Outline thickness. Very high values can get clipped by the font atlas padding.")]
    [Range(0f, 1f)] public float outlineThickness = 0.25f;

    [Header("Direction")]
    [Tooltip("Which way the character faces. If the character is flipped with a negative X scale and words spawn under it, this is mirrored automatically.")]
    public Facing facing = Facing.Right;
    [Tooltip("Direction the words fly, in degrees. 0 = straight forward (facing side), 90 = straight up, 180 = backwards.")]
    [Range(-90f, 180f)] public float emitAngle = 40f;
    [Tooltip("Random variation added to the angle (+/- degrees).")]
    [Range(0f, 90f)] public float angleSpread = 20f;
    [Tooltip("Moves the point where words appear, in canvas units. Use it to line the spawn point up with the mouth.\n" +
             "X = toward the facing side (flips with Facing and with a mirrored character), Y = up.\n" +
             "Select the object to see the spawn point as a yellow gizmo in the Scene view.")]
    public Vector2 spawnOffset = Vector2.zero;
    [Tooltip("Random offset around the spawn point where words appear (canvas units, +/- X and Y).")]
    public Vector2 spawnJitter = new Vector2(6f, 6f);

    [Header("Motion")]
    [Tooltip("Initial speed in canvas units per second (min, max).")]
    public Vector2 speed = new Vector2(200f, 300f);
    [Tooltip("How quickly words slow down after popping out. 0 = keep flying at full speed.")]
    [Min(0f)] public float drag = 2.5f;
    [Tooltip("Upward drift acceleration (canvas units/sec²). Negative values make words fall.")]
    public float floatUp = 40f;
    [Tooltip("Side-to-side sway distance (canvas units).")]
    [Min(0f)] public float swayAmount = 6f;
    [Tooltip("Sway speed.")]
    [Min(0f)] public float swaySpeed = 8f;

    [Header("Rotation")]
    [Tooltip("Random tilt per word in degrees (min, max).")]
    public Vector2 tilt = new Vector2(5f, 18f);
    [Tooltip("Tilt each word the opposite way to the previous one (classic cartoon zig-zag).")]
    public bool alternateTilt = true;
    [Tooltip("Rocking back and forth while alive (degrees).")]
    [Min(0f)] public float wobbleAngle = 8f;
    [Tooltip("Rocking speed.")]
    [Min(0f)] public float wobbleSpeed = 14f;

    [Header("Pop & Bounce")]
    [Tooltip("Seconds for a word to pop in from nothing to full size.")]
    [Min(0f)] public float popDuration = 0.18f;
    [Tooltip("How far the pop overshoots before settling. 0 = no overshoot, 2-3 = bouncy.")]
    [Range(0f, 5f)] public float popOvershoot = 2.5f;
    [Tooltip("Squash & stretch strength while alive.")]
    [Range(0f, 0.5f)] public float squashAmount = 0.08f;
    [Tooltip("Squash & stretch speed.")]
    [Min(0f)] public float squashSpeed = 18f;
    [Tooltip("Letters hop individually, like they're laughing too.")]
    public bool letterHop = true;
    [Tooltip("Hop height as a fraction of the font size.")]
    [Range(0f, 1f)] public float letterHopHeight = 0.12f;
    [Tooltip("Hop speed.")]
    [Min(0f)] public float letterHopSpeed = 16f;
    [Tooltip("Timing offset between neighbouring letters. 0 = all letters hop together.")]
    public float letterHopOffset = 1.1f;

    [Header("Lifetime & Fade")]
    [Tooltip("How long each word lives in seconds (min, max).")]
    public Vector2 lifetime = new Vector2(0.9f, 1.3f);
    [Tooltip("Seconds of fading at the end of the lifetime.")]
    [Min(0f)] public float fadeDuration = 0.35f;
    [Tooltip("Scale reached at the very end of the fade. 1 = no shrink, below 1 = shrinks, above 1 = grows.")]
    [Range(0f, 2f)] public float endScale = 0.6f;

    [Header("Advanced")]
    [Tooltip("Where words are parented. Empty = this object (words follow the character). " +
             "Set a higher container to leave words behind when the character moves, or to draw them above other UI.")]
    public RectTransform spawnParent;
    [Tooltip("Max words on screen at once. The oldest word is recycled when the limit is hit.")]
    [Min(1)] public int maxWords = 12;

    // ------------------------------------------------------------------
    // RUNTIME STATE
    // ------------------------------------------------------------------

    class Word
    {
        public RectTransform rt;
        public TextMeshProUGUI tmp;
        public bool alive;
        public float age, life;
        public Vector2 origin;        // mouth position in the parent's local space
        public Vector2 offset, vel;   // flight from the origin, in canvas units
        public Vector2 dir;
        public Transform canvasRoot;  // reference for "canvas units"
        public float tilt, seed, mirror;
        public Color color;
        public float lastAlpha;
    }

    enum State { Idle, Waiting, Laughing }

    // Never let the scale hit exactly 0: TMP bakes the SDF scale from lossyScale
    // when it builds the mesh, and a 0 scale leaves the text permanently blurry.
    const float MinScale = 0.01f;

    readonly List<Word> pool = new List<Word>();
    State state = State.Idle;
    float timer;
    int remaining;
    int spawnCount;
    int wordIndex;
    int paletteIndex;

    TMP_FontAsset activeFont;
    Material activeMaterial;
    Material outlineMaterial;     // runtime copy of the font material, owned by this component
    TMP_FontAsset outlineMaterialFont;
    bool lookDirty = true;
    bool warnedNoFont;

    RectTransform Parent => spawnParent != null ? spawnParent : (RectTransform)transform;

    /// <summary>True while a laugh is running or waiting to start.</summary>
    public bool IsLaughing => state != State.Idle;

    // ------------------------------------------------------------------
    // PUBLIC API
    // ------------------------------------------------------------------

    /// <summary>Start laughing (after Start Delay). Restarts if already laughing.</summary>
    [ContextMenu("Play Laugh")]
    public void Play()
    {
        if (!WarnIfEditMode()) return;
        state = State.Waiting;
        timer = startDelay;
    }

    /// <summary>Stop emitting new words. Words already on screen finish naturally.</summary>
    [ContextMenu("Stop")]
    public void Stop()
    {
        state = State.Idle;
    }

    /// <summary>Stop emitting and remove every word instantly.</summary>
    public void StopImmediately()
    {
        state = State.Idle;
        foreach (var w in pool) Hide(w);
    }

    /// <summary>Emit a single word right now, independent of the laugh sequence.</summary>
    [ContextMenu("Emit One Word")]
    public void EmitWord()
    {
        if (!WarnIfEditMode()) return;
        SpawnWord();
    }

    // ------------------------------------------------------------------
    // LIFECYCLE
    // ------------------------------------------------------------------

    void Reset()
    {
        // Gradient keys are set here instead of in the field initializer, because
        // field initializers can run off the main thread during deserialization.
        gradient = new Gradient();
        gradient.SetKeys(
            new[]
            {
                new GradientColorKey(new Color(1f, 0.85f, 0.2f), 0f),
                new GradientColorKey(new Color(1f, 0.45f, 0.2f), 0.5f),
                new GradientColorKey(new Color(1f, 0.4f, 0.65f), 1f),
            },
            new[] { new GradientAlphaKey(1f, 0f), new GradientAlphaKey(1f, 1f) });
    }

    void OnEnable()
    {
        if (playOnEnable) Play();
    }

    void OnDisable()
    {
        StopImmediately();
    }

    void OnDestroy()
    {
        foreach (var w in pool)
            if (w.rt != null) Destroy(w.rt.gameObject);
        pool.Clear();

        if (outlineMaterial != null) Destroy(outlineMaterial);
    }

    void OnValidate()
    {
        wordsPerLaugh.x = Mathf.Max(1, wordsPerLaugh.x);
        wordsPerLaugh.y = Mathf.Max(wordsPerLaugh.x, wordsPerLaugh.y);
        wordInterval = ClampRange(wordInterval, 0.01f);
        loopDelay = ClampRange(loopDelay, 0f);
        maxFontSize = Mathf.Max(minFontSize, maxFontSize);
        speed = ClampRange(speed, 0f);
        tilt = ClampRange(tilt, 0f);
        lifetime = ClampRange(lifetime, 0.05f);
        spawnJitter = Vector2.Max(spawnJitter, Vector2.zero);

        // Font / outline changes are applied on the next Update. Touching TMP
        // objects directly from OnValidate triggers SendMessage warnings.
        lookDirty = true;
    }

    void Update()
    {
        float dt = useUnscaledTime ? Time.unscaledDeltaTime : Time.deltaTime;

        if (lookDirty) RefreshLook();

        TickSequence(dt);
        TickWords(dt);
    }

    // ------------------------------------------------------------------
    // SEQUENCE
    // ------------------------------------------------------------------

    void TickSequence(float dt)
    {
        if (state == State.Idle) return;

        timer -= dt;
        if (timer > 0f) return;

        if (state == State.Waiting) BeginLaugh();

        SpawnWord();
        remaining--;

        if (remaining > 0)
        {
            timer = RandomIn(wordInterval);
        }
        else if (loop)
        {
            state = State.Waiting;
            timer = RandomIn(loopDelay);
        }
        else
        {
            state = State.Idle;
        }
    }

    void BeginLaugh()
    {
        state = State.Laughing;
        remaining = Random.Range(wordsPerLaugh.x, wordsPerLaugh.y + 1);
        wordIndex = 0;
    }

    // ------------------------------------------------------------------
    // WORDS
    // ------------------------------------------------------------------

    void SpawnWord()
    {
        if (words == null || words.Length == 0) return;

        TMP_FontAsset wanted = font != null ? font : TMP_Settings.defaultFontAsset;
        if (lookDirty || wanted != activeFont || activeMaterial == null) RefreshLook();
        if (activeFont == null)
        {
            if (!warnedNoFont)
            {
                Debug.LogWarning("[EmoteLaugh] No font assigned and no TMP default font found.", this);
                warnedNoFont = true;
            }
            return;
        }

        Word w = GetFreeWord();
        RectTransform parent = Parent;
        if (w.rt.parent != parent) w.rt.SetParent(parent, false);
        ApplyLook(w);

        w.tmp.text = NextWord();
        w.tmp.fontSize = Random.Range(minFontSize, Mathf.Max(minFontSize, maxFontSize));
        w.color = NextColor();
        w.tmp.color = w.color;
        w.lastAlpha = w.color.a;

        w.canvasRoot = FindCanvasRoot(parent);

        // Spawn point (mouth + Spawn Offset) in the parent's local space.
        w.origin = parent.InverseTransformPoint(SpawnPointWorld(FindCanvasRoot(transform)));
        w.offset = new Vector2(Random.Range(-spawnJitter.x, spawnJitter.x),
                               Random.Range(-spawnJitter.y, spawnJitter.y));

        float angle = (emitAngle + Random.Range(-angleSpread, angleSpread)) * Mathf.Deg2Rad;
        Vector2 dir = new Vector2(Mathf.Cos(angle), Mathf.Sin(angle));
        if (facing == Facing.Left) dir.x = -dir.x;

        float tiltSign = alternateTilt
            ? (spawnCount % 2 == 0 ? 1f : -1f)
            : (Random.value < 0.5f ? -1f : 1f);

        w.dir = dir;
        w.vel = dir * RandomIn(speed);
        w.tilt = tiltSign * RandomIn(tilt);
        w.seed = Random.value * 100f;
        // Keep the text readable if the parent is mirrored with a negative X scale.
        w.mirror = parent.lossyScale.x < 0f ? -1f : 1f;
        w.age = 0f;
        w.life = RandomIn(lifetime);
        w.alive = true;

        w.rt.SetAsLastSibling(); // newest word on top
        w.tmp.enabled = true;
        spawnCount++;

        Pose(w);
    }

    void TickWords(float dt)
    {
        for (int i = 0; i < pool.Count; i++)
        {
            Word w = pool[i];
            if (!w.alive) continue;
            if (w.rt == null) { w.alive = false; continue; }

            w.age += dt;
            if (w.age >= w.life)
            {
                Hide(w);
                continue;
            }

            w.vel *= Mathf.Exp(-drag * dt);
            w.vel.y += floatUp * dt;
            w.offset += w.vel * dt;

            Pose(w);
        }
    }

    /// <summary>Applies position, rotation, scale, alpha and letter hop for the word's current age.</summary>
    void Pose(Word w)
    {
        float t = w.age;

        // Converts canvas units to the parent's local units. Recomputed every frame
        // so the words stay the same size even if the character's scale is animated.
        Vector2 k = LocalUnitsPerCanvasUnit(w);

        // Position: flight path plus a sway perpendicular to the flight direction.
        Vector2 perp = new Vector2(-w.dir.y, w.dir.x);
        Vector2 sway = perp * (Mathf.Sin(t * swaySpeed + w.seed) * swayAmount);
        w.rt.localPosition = w.origin + Vector2.Scale(w.offset + sway, k);

        // Rotation: base tilt plus rocking.
        float rot = w.tilt + Mathf.Sin(t * wobbleSpeed + w.seed * 1.3f) * wobbleAngle;
        w.rt.localRotation = Quaternion.Euler(0f, 0f, rot);

        // Scale: pop in, squash & stretch, shrink during the fade.
        float pop = popDuration > 0f ? EaseOutBack(Mathf.Clamp01(t / popDuration), popOvershoot) : 1f;
        float fade = fadeDuration > 0f
            ? Mathf.Clamp01((t - (w.life - fadeDuration)) / fadeDuration)
            : 0f;
        float s = pop * Mathf.Lerp(1f, endScale, fade) * sizeMultiplier;
        float squash = Mathf.Sin(t * squashSpeed + w.seed) * squashAmount;
        float sx = Mathf.Max(MinScale, s * (1f + squash));
        float sy = Mathf.Max(MinScale, s * (1f - squash));
        w.rt.localScale = new Vector3(sx * k.x * w.mirror, sy * k.y, 1f);

        // Alpha: only touch TMP when it actually changes (each change rebuilds the mesh).
        float a = w.color.a * (1f - fade);
        if (!Mathf.Approximately(a, w.lastAlpha))
        {
            w.tmp.alpha = a;
            w.lastAlpha = a;
        }

        if (letterHop && letterHopHeight > 0f) HopLetters(w);
    }

    void HopLetters(Word w)
    {
        TextMeshProUGUI tmp = w.tmp;
        tmp.ForceMeshUpdate();

        TMP_TextInfo info = tmp.textInfo;
        float height = letterHopHeight * tmp.fontSize;

        for (int i = 0; i < info.characterCount; i++)
        {
            TMP_CharacterInfo c = info.characterInfo[i];
            if (!c.isVisible) continue;

            // Abs(sin) gives a bouncy "hop off the floor" instead of a smooth wave.
            float hop = Mathf.Abs(Mathf.Sin(w.age * letterHopSpeed + i * letterHopOffset + w.seed)) * height;
            Vector3 offset = new Vector3(0f, hop, 0f);

            Vector3[] verts = info.meshInfo[c.materialReferenceIndex].vertices;
            int vi = c.vertexIndex;
            verts[vi] += offset;
            verts[vi + 1] += offset;
            verts[vi + 2] += offset;
            verts[vi + 3] += offset;
        }

        tmp.UpdateVertexData(TMP_VertexDataUpdateFlags.Vertices);
    }

    void Hide(Word w)
    {
        w.alive = false;
        if (w.tmp != null) w.tmp.enabled = false;
    }

    Word GetFreeWord()
    {
        pool.RemoveAll(p => p.rt == null);

        Word oldest = null;
        foreach (var w in pool)
        {
            if (!w.alive) return w;
            if (oldest == null || w.age > oldest.age) oldest = w;
        }

        if (pool.Count < maxWords || oldest == null) return CreateWord();
        return oldest; // limit reached: recycle the oldest word on screen
    }

    Word CreateWord()
    {
        var go = new GameObject("EmoteLaugh_Word", typeof(RectTransform));
        go.layer = gameObject.layer;

        var rt = (RectTransform)go.transform;
        rt.SetParent(Parent, false);
        rt.anchorMin = rt.anchorMax = rt.pivot = new Vector2(0.5f, 0.5f);
        rt.sizeDelta = new Vector2(200f, 100f);

        var tmp = go.AddComponent<TextMeshProUGUI>();
        tmp.raycastTarget = false;
        tmp.richText = true;
        tmp.alignment = TextAlignmentOptions.Center;
        tmp.textWrappingMode = TextWrappingModes.NoWrap;
        tmp.overflowMode = TextOverflowModes.Overflow;
        tmp.extraPadding = true;
        tmp.enabled = false;

        var w = new Word { rt = rt, tmp = tmp };
        pool.Add(w);
        return w;
    }

    // ------------------------------------------------------------------
    // LOOK (font, style, outline material)
    // ------------------------------------------------------------------

    void RefreshLook()
    {
        lookDirty = false;
        activeFont = font != null ? font : TMP_Settings.defaultFontAsset;

        Material stale = null;

        if (activeFont == null || activeFont.material == null)
        {
            activeMaterial = null;
        }
        else if (outline)
        {
            if (outlineMaterial == null || outlineMaterialFont != activeFont)
            {
                stale = outlineMaterial;
                outlineMaterial = new Material(activeFont.material)
                {
                    name = activeFont.material.name + " (EmoteLaugh)",
                    hideFlags = HideFlags.DontSave
                };
                outlineMaterialFont = activeFont;
            }

            // OUTLINE_ON is needed by the Mobile SDF shader; the regular SDF shader ignores it.
            outlineMaterial.EnableKeyword(ShaderUtilities.Keyword_Outline);
            if (outlineMaterial.HasProperty(ShaderUtilities.ID_OutlineColor))
                outlineMaterial.SetColor(ShaderUtilities.ID_OutlineColor, outlineColor);
            if (outlineMaterial.HasProperty(ShaderUtilities.ID_OutlineWidth))
                outlineMaterial.SetFloat(ShaderUtilities.ID_OutlineWidth, outlineThickness);

            activeMaterial = outlineMaterial;
        }
        else
        {
            stale = outlineMaterial;
            outlineMaterial = null;
            outlineMaterialFont = null;
            activeMaterial = activeFont.material;
        }

        // Re-apply to every pooled word so live words pick up inspector tweaks
        // and nothing keeps pointing at a material that is about to be destroyed.
        if (activeMaterial != null)
            foreach (var w in pool)
                if (w.tmp != null) ApplyLook(w);

        if (stale != null) Destroy(stale);
    }

    void ApplyLook(Word w)
    {
        TextMeshProUGUI tmp = w.tmp;
        // Font first: assigning a font resets the material to the font's default.
        if (tmp.font != activeFont) tmp.font = activeFont;
        if (tmp.fontSharedMaterial != activeMaterial) tmp.fontSharedMaterial = activeMaterial;
        tmp.fontStyle = fontStyle;
        tmp.characterSpacing = characterSpacing;
        tmp.UpdateMeshPadding(); // outline thickness affects padding
    }

    // ------------------------------------------------------------------
    // HELPERS
    // ------------------------------------------------------------------

    string NextWord()
    {
        if (wordOrder == WordOrder.Sequential)
            return words[wordIndex++ % words.Length];
        return words[Random.Range(0, words.Length)];
    }

    Color NextColor()
    {
        bool hasPalette = palette != null && palette.Length > 0;
        switch (colorMode)
        {
            case ColorMode.RandomFromPalette:
                return hasPalette ? palette[Random.Range(0, palette.Length)] : textColor;
            case ColorMode.CyclePalette:
                return hasPalette ? palette[paletteIndex++ % palette.Length] : textColor;
            case ColorMode.RandomFromGradient:
                return gradient != null ? gradient.Evaluate(Random.value) : textColor;
            default:
                return textColor;
        }
    }

    bool WarnIfEditMode()
    {
        if (Application.isPlaying) return true;
        Debug.Log("[EmoteLaugh] Enter Play Mode to preview the laugh.", this);
        return false;
    }

    /// <summary>
    /// How many of the parent's local units make one canvas unit, per axis.
    /// (1,1) when Ignore Parent Scale is off. A character scaled 200x gives (0.005, 0.005).
    /// </summary>
    Vector2 LocalUnitsPerCanvasUnit(Word w) => LocalUnitsPerCanvasUnit(w.rt.parent, w.canvasRoot);

    Vector2 LocalUnitsPerCanvasUnit(Transform space, Transform canvasRoot)
    {
        if (!ignoreParentScale || space == null) return Vector2.one;

        Vector3 p = space.lossyScale;
        Vector3 c = canvasRoot != null ? canvasRoot.lossyScale : Vector3.one;
        return new Vector2(SafeRatio(c.x, p.x), SafeRatio(c.y, p.y));
    }

    static Transform FindCanvasRoot(Transform t)
    {
        Canvas canvas = t.GetComponentInParent<Canvas>();
        return canvas != null ? canvas.rootCanvas.transform : null;
    }

    /// <summary>
    /// World position of this object plus Spawn Offset. The offset is applied in this object's
    /// own space, so it follows the character's rotation and mirroring even when Spawn Parent is set.
    /// </summary>
    Vector3 SpawnPointWorld(Transform canvasRoot)
    {
        Vector2 off = spawnOffset;
        if (facing == Facing.Left) off.x = -off.x;
        return transform.TransformPoint(Vector2.Scale(off, LocalUnitsPerCanvasUnit(transform, canvasRoot)));
    }

    void OnDrawGizmosSelected()
    {
        Transform root = FindCanvasRoot(transform);
        float unit = root != null ? Mathf.Abs(root.lossyScale.x) : 1f; // one canvas unit in world space
        if (unit < 1e-6f) unit = 1f;

        Vector3 p = SpawnPointWorld(root);
        Gizmos.color = Color.yellow;
        Gizmos.DrawWireSphere(p, 10f * unit);

        // Emit direction (centre of the spread), 60 canvas units long.
        // Mirrors in the parent's space the same way the words do.
        Transform parent = Parent;
        float a = emitAngle * Mathf.Deg2Rad;
        Vector2 dir = new Vector2(Mathf.Cos(a), Mathf.Sin(a));
        if (facing == Facing.Left) dir.x = -dir.x;
        Vector2 k = LocalUnitsPerCanvasUnit(parent, FindCanvasRoot(parent));
        Gizmos.DrawLine(p, p + parent.TransformVector(Vector2.Scale(dir * 60f, k)));
    }

    // Falls back to 1 when either scale is ~0 (e.g. a panel animating in from scale 0).
    static float SafeRatio(float canvas, float parent)
    {
        canvas = Mathf.Abs(canvas);
        parent = Mathf.Abs(parent);
        return canvas > 1e-6f && parent > 1e-6f ? canvas / parent : 1f;
    }

    static float RandomIn(Vector2 range) => Random.Range(range.x, range.y);

    static Vector2 ClampRange(Vector2 range, float floor)
    {
        range.x = Mathf.Max(floor, range.x);
        range.y = Mathf.Max(range.x, range.y);
        return range;
    }

    /// <summary>Ease-out with overshoot. overshoot = 0 gives a plain cubic ease-out.</summary>
    static float EaseOutBack(float t, float overshoot)
    {
        float u = t - 1f;
        return 1f + (overshoot + 1f) * u * u * u + overshoot * u * u;
    }
}
