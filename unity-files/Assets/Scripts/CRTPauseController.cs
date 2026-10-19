using System.Collections;
using System.Collections.Generic;
using TMPro;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;
using UnityEngine.UI;

/// <summary>
/// CRT "zoom out to the TV" effect.
///
///   ActivateEffect()   -> enables the overlay UI, zooms out to reveal the CRT TV.
///   DeactivateEffect() -> zooms back in, then restores everything it touched.
///
/// Put this on an EMPTY GameObject that stays active (not on the RawImage or its parents).
/// The RawImage / its canvas may be disabled in the scene; this script enables them
/// before the effect starts and puts them back to their original state afterwards.
/// </summary>
public class CRTPauseController : MonoBehaviour
{
    [Header("References")]
    public Camera gameCamera;
    [Tooltip("Full-screen RawImage on a Screen Space - Overlay canvas with a high Sort Order. May start disabled.")]
    public RawImage overlay;
    public TextMeshProUGUI textMeshProUGUI;
    [Tooltip("Material using the UI/CRTPauseOverlay shader.")]
    public Material crtMaterial;

    [Header("Transition")]
    public float duration = 1.2f;
    public AnimationCurve ease = AnimationCurve.EaseInOut(0, 0, 1, 1);
    [Tooltip("Also set Time.timeScale = 0 while active (restored afterwards). Leave OFF if your own pause logic handles time.")]
    public bool freezeTime = false;

    [Header("CRT feel")]
    [Tooltip("Vertical resolution the game is rendered at inside the TV. Lower = chunkier.")]
    public int renderHeight = 540;
    public FilterMode filterMode = FilterMode.Bilinear;
    [Tooltip("How often the TV image updates. 0 = every frame.")]
    public float displayFps = 24f;

    [Header("Post-processing (temporary, never touches DefaultVolumeProfile)")]
    [Tooltip("Adds a runtime global Volume on top of your profile while the effect is active, then removes it.")]
    public bool useVolumeEffects = true;
    [Tooltip("Must be included in the game camera's Volume Mask.")]
    public int volumeLayer = 0;
    public float bloomIntensity = 0.6f;
    public float bloomThreshold = 0.9f;
    [Range(0f, 1f)] public float chromaticAberration = 0.15f;

    // NEW: message text animation (unscaled time, runs while paused)
    [Header("Message animation")]
    [SerializeField] float typeCharsPerSecond = 45f;
    [SerializeField] float messageFadeInTime = 0.2f;
    [SerializeField] float messageFadeOutTime = 0.25f;
    [SerializeField] float messageEntryScale = 1.15f; // pops from this size down to normal
    [SerializeField] float messageExitScale = 0.85f;  // shrinks to this size while fading out
    [SerializeField] float dotInterval = 0.35f;

    public bool IsEffectActive { get; private set; }

    // NEW: true when fully zoomed back in and camera/overlay restored (no transition running)
    public bool IsFullyIdle()
    {
        return !stateSaved;
    }

    // runtime
    RenderTexture rt;
    Material mat;
    float amount;                 // 0 = normal game, 1 = fully zoomed out CRT
    bool rendering;               // overlay is being shown / camera is captured
    float nextRenderTime;
    Coroutine anim;

    // NEW: message animation state
    Coroutine messageAnim;
    bool messagePlaying; // set before StartCoroutine, so a routine that ends instantly still clears it
    Vector3 messageBaseScale = Vector3.one;
    Color defaultMessageColor = Color.white;

    // helper camera so the Game view never reports "No cameras rendering"
    Camera screenCamera;

    // temporary volume
    Volume volume;
    VolumeProfile volumeProfile;

    // saved state so we can restore it
    bool stateSaved;
    bool camWasEnabled, overlayWasEnabled, canvasWasEnabled;
    Canvas overlayCanvas;
    readonly List<GameObject> activatedObjects = new List<GameObject>();
    bool timeFrozen;
    float savedTimeScale = 1f;

    // ------------------------------------------------------------------ public API

    /// <summary>Enable the overlay and start the zoom-out to the CRT TV.</summary>
    [ContextMenu("Activate Effect")]
    public void ActivateEffect(string message = "")
    {
        if (IsEffectActive) return;
        IsEffectActive = true;

        BeginCapture();
        // UPDATED: animated entry, empty = no text
        StopMessageAnim();
        ResetMessage();
        if (!string.IsNullOrEmpty(message))
        {
            SetMessage(message, defaultMessageColor, false);
        }
        if (freezeTime && !timeFrozen)
        {
            savedTimeScale = Time.timeScale;
            Time.timeScale = 0f;
            timeFrozen = true;
        }

        StartAnim(1f);
    }

    /// <summary>Zoom back into the game, then restore camera, overlay, volume and time scale.</summary>
    [ContextMenu("Deactivate Effect")]
    public void DeactivateEffect()
    {
        if (!IsEffectActive) return;
        IsEffectActive = false;
        HideMessage(); // UPDATED: fade text out instead of clearing it
        // Un-freeze right away so the game runs (choppily) inside the TV while zooming back in.
        RestoreTime();

        StartAnim(0f);
    }

    // NEW: fade the current message out, then type the new one in (animatedDots = looping "...")
    public void SetMessage(string message, Color color, bool animatedDots)
    {
        StopMessageAnim();
        messagePlaying = true;
        messageAnim = StartCoroutine(MessageRoutine(message, color, animatedDots));
    }

    // NEW: fade the current message out
    public void HideMessage()
    {
        SetMessage("", defaultMessageColor, false);
    }

    // NEW: true while a message is fading or typing (looping dots never finish)
    public bool IsMessageAnimating()
    {
        return messagePlaying;
    }

    // ------------------------------------------------------------------ unity

    void Awake()
    {
        if (!gameCamera) gameCamera = Camera.main;

        mat = new Material(crtMaterial);
        overlay.material = mat;          // works even if the overlay object is inactive
        overlay.raycastTarget = false;

        if (useVolumeEffects) BuildVolume();
        BuildScreenCamera();

        // NEW: remember the text's scene look, start empty
        messageBaseScale = textMeshProUGUI.rectTransform.localScale;
        defaultMessageColor = textMeshProUGUI.color;
        ResetMessage();
    }

    void Update()
    {
        if (!rendering) return;

        // Re-render the game into the TV at the "CRT" frame rate (unscaled time, so it works while paused).
        if (displayFps <= 0f || Time.unscaledTime >= nextRenderTime)
        {
            gameCamera.Render();
            nextRenderTime = Time.unscaledTime + (displayFps > 0f ? 1f / displayFps : 0f);
        }
    }

    void LateUpdate()
    {
        if (rendering) ApplyProperties();
    }

    void OnDisable()
    {
        // Never leave the game in a modified state.
        if (stateSaved) EndEffect();
    }

    void OnDestroy()
    {
        if (rt) { rt.Release(); Destroy(rt); rt = null; }
        if (mat) Destroy(mat);
        if (volumeProfile) Destroy(volumeProfile);
    }

    // ------------------------------------------------------------------ setup / teardown

    void BeginCapture()
    {
        SaveStateAndEnableOverlay();     // overlay UI is enabled BEFORE anything else happens

        int h = Mathf.Max(64, renderHeight);
        int w = Mathf.RoundToInt(h * (float)Screen.width / Screen.height);
        if (rt == null || rt.width != w || rt.height != h)
        {
            if (rt) { rt.Release(); Destroy(rt); }
            rt = new RenderTexture(w, h, 24) { name = "CRT_Capture", filterMode = filterMode, wrapMode = TextureWrapMode.Clamp };
            rt.Create();
        }

        gameCamera.enabled = false;      // we render it manually at displayFps
        gameCamera.targetTexture = rt;

        screenCamera.targetDisplay = gameCamera.targetDisplay;
        screenCamera.enabled = true;     // keeps something rendering to the screen

        if (volume)
        {
            volume.weight = amount;      // starts at 0, so no visible pop
            volume.enabled = true;
        }

        gameCamera.Render();             // make sure the first overlay frame isn't empty
        overlay.texture = rt;
        ApplyProperties();

        rendering = true;
        nextRenderTime = Time.unscaledTime + (displayFps > 0f ? 1f / displayFps : 0f);
    }

    void SaveStateAndEnableOverlay()
    {
        if (stateSaved) return;
        stateSaved = true;

        camWasEnabled = gameCamera.enabled;
        overlayWasEnabled = overlay.enabled;

        // Enable the overlay object and any disabled parents (so it's really visible).
        activatedObjects.Clear();
        for (Transform t = overlay.transform; t != null; t = t.parent)
        {
            if (!t.gameObject.activeSelf)
            {
                t.gameObject.SetActive(true);
                activatedObjects.Add(t.gameObject);
            }
        }

        overlayCanvas = overlay.GetComponentInParent<Canvas>(true);
        if (overlayCanvas)
        {
            canvasWasEnabled = overlayCanvas.enabled;
            overlayCanvas.enabled = true;
        }

        overlay.enabled = true;
    }

    void EndEffect()
    {
        rendering = false;
        IsEffectActive = false;
        amount = 0f;

        RestoreTime();

        if (volume)
        {
            volume.weight = 0f;
            volume.enabled = false;
        }

        if (gameCamera)
        {
            gameCamera.targetTexture = null;
            gameCamera.enabled = camWasEnabled;
        }
        if (screenCamera) screenCamera.enabled = false;

        if (overlay)
        {
            overlay.enabled = overlayWasEnabled;
            if (overlayCanvas) overlayCanvas.enabled = canvasWasEnabled;
            foreach (var go in activatedObjects)
                if (go) go.SetActive(false);   // back to how it was in the scene
        }
        activatedObjects.Clear();
        stateSaved = false;

        // NEW: never leave a half-faded message behind
        StopMessageAnim();
        ResetMessage();
    }

    void RestoreTime()
    {
        if (!timeFrozen) return;
        Time.timeScale = savedTimeScale;
        timeFrozen = false;
    }

    // Renders nothing (just clears to black) while the game camera is busy drawing into the TV.
    void BuildScreenCamera()
    {
        var go = new GameObject("CRT_ScreenCamera");
        go.transform.SetParent(transform, false);

        screenCamera = go.AddComponent<Camera>();
        screenCamera.clearFlags = CameraClearFlags.SolidColor;
        screenCamera.backgroundColor = Color.black;
        screenCamera.cullingMask = 0;
        screenCamera.orthographic = true;
        screenCamera.depth = -100f;
        screenCamera.allowHDR = false;
        screenCamera.allowMSAA = false;
        screenCamera.enabled = false;
    }

    // Runtime-only Volume. Your DefaultVolumeProfile asset is never modified:
    // this Volume just blends on top of it (weight 0 -> 1) and is switched off afterwards.
    void BuildVolume()
    {
        var go = new GameObject("CRT_TemporaryVolume");
        go.transform.SetParent(transform, false);
        go.layer = volumeLayer;

        volume = go.AddComponent<Volume>();
        volume.isGlobal = true;
        volume.priority = 1000f;
        volume.weight = 0f;

        volumeProfile = ScriptableObject.CreateInstance<VolumeProfile>();

        var bloom = volumeProfile.Add<Bloom>(false);
        bloom.intensity.Override(bloomIntensity);
        bloom.threshold.Override(bloomThreshold);

        var ca = volumeProfile.Add<ChromaticAberration>(false);
        ca.intensity.Override(chromaticAberration);

        volume.sharedProfile = volumeProfile;
        volume.enabled = false;
    }

    // ------------------------------------------------------------------ animation

    void StartAnim(float target)
    {
        if (anim != null) StopCoroutine(anim);
        anim = StartCoroutine(Animate(target));
    }

    IEnumerator Animate(float target)
    {
        float from = amount;
        float dur = Mathf.Max(0.01f, duration * Mathf.Abs(target - from));
        float time = 0f;

        while (time < dur)
        {
            time += Time.unscaledDeltaTime;
            amount = Mathf.LerpUnclamped(from, target, ease.Evaluate(Mathf.Clamp01(time / dur)));
            yield return null;
        }

        amount = target;
        anim = null;

        if (target <= 0f) EndEffect();   // fully zoomed back in: restore everything
    }

    void ApplyProperties()
    {
        mat.SetFloat("_Amount", amount);
        mat.SetFloat("_Aspect", (float)Screen.width / Screen.height);
        mat.SetFloat("_UTime", Time.unscaledTime);
        if (rt) mat.SetVector("_PixelSize", new Vector4(1f / rt.width, 1f / rt.height, 0, 0));

        if (volume) volume.weight = Mathf.Clamp01(amount);
    }

    // ------------------------------------------------------------------ message

    // NEW: fade old text out, then fade/pop/type the new text in
    IEnumerator MessageRoutine(string message, Color color, bool animatedDots)
    {
        if (!string.IsNullOrEmpty(textMeshProUGUI.text))
        {
            yield return FadeOutMessage();
        }
        if (string.IsNullOrEmpty(message))
        {
            messagePlaying = false;
            yield break;
        }

        // dots are in the text from the start (hidden), so the centered line never shifts
        string fullText = message;
        if (animatedDots)
        {
            fullText = message + "...";
        }
        RectTransform rect = textMeshProUGUI.rectTransform;
        textMeshProUGUI.text = fullText;
        textMeshProUGUI.color = color;
        textMeshProUGUI.alpha = 0f;
        textMeshProUGUI.maxVisibleCharacters = 0;

        int typedCount = message.Length;
        float typeTime = 0f;
        if (typeCharsPerSecond > 0f)
        {
            typeTime = typedCount / typeCharsPerSecond;
        }
        float entryTime = Mathf.Max(messageFadeInTime, typeTime);

        float time = 0f;
        while (time < entryTime)
        {
            time += Time.unscaledDeltaTime;

            float fade = 1f;
            if (messageFadeInTime > 0f)
            {
                fade = Mathf.Clamp01(time / messageFadeInTime);
            }
            textMeshProUGUI.alpha = color.a * fade;
            rect.localScale = Vector3.Lerp(messageBaseScale * messageEntryScale, messageBaseScale, Mathf.SmoothStep(0f, 1f, fade));

            int shown = typedCount;
            if (typeTime > 0f)
            {
                shown = Mathf.Min(typedCount, Mathf.FloorToInt(time * typeCharsPerSecond));
            }
            textMeshProUGUI.maxVisibleCharacters = shown;
            yield return null;
        }
        textMeshProUGUI.alpha = color.a;
        rect.localScale = messageBaseScale;
        textMeshProUGUI.maxVisibleCharacters = typedCount;

        if (!animatedDots)
        {
            messagePlaying = false;
            yield break;
        }

        // cycles "", ".", "..", "..." until the message is replaced
        int dots = 0;
        float waited = 0f;
        while (true)
        {
            yield return null;
            waited += Time.unscaledDeltaTime;
            if (waited >= dotInterval)
            {
                waited = 0f;
                dots = (dots + 1) % 4;
                textMeshProUGUI.maxVisibleCharacters = typedCount + dots;
            }
        }
    }

    // NEW: fade + shrink the current text, then clear it
    IEnumerator FadeOutMessage()
    {
        RectTransform rect = textMeshProUGUI.rectTransform;
        float startAlpha = textMeshProUGUI.alpha;
        Vector3 startScale = rect.localScale;
        Vector3 endScale = messageBaseScale * messageExitScale;

        float time = 0f;
        while (time < messageFadeOutTime)
        {
            time += Time.unscaledDeltaTime;
            float t = Mathf.Clamp01(time / messageFadeOutTime);
            textMeshProUGUI.alpha = Mathf.Lerp(startAlpha, 0f, t);
            rect.localScale = Vector3.Lerp(startScale, endScale, t);
            yield return null;
        }
        ResetMessage();
    }

    // NEW
    void StopMessageAnim()
    {
        if (messageAnim != null)
        {
            StopCoroutine(messageAnim);
            messageAnim = null;
        }
        messagePlaying = false;
    }

    // NEW: empty text at normal size and color
    void ResetMessage()
    {
        if (textMeshProUGUI == null)
        {
            return;
        }
        textMeshProUGUI.text = "";
        textMeshProUGUI.color = defaultMessageColor;
        textMeshProUGUI.maxVisibleCharacters = 99999; // TMP default
        textMeshProUGUI.rectTransform.localScale = messageBaseScale;
    }
}