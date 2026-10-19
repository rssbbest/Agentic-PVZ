using System.Collections;
using System.Collections.Generic;
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

    public bool IsEffectActive { get; private set; }

    // runtime
    RenderTexture rt;
    Material mat;
    float amount;                 // 0 = normal game, 1 = fully zoomed out CRT
    bool rendering;               // overlay is being shown / camera is captured
    float nextRenderTime;
    Coroutine anim;

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
    public void ActivateEffect()
    {
        if (IsEffectActive) return;
        IsEffectActive = true;

        BeginCapture();

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

        // Un-freeze right away so the game runs (choppily) inside the TV while zooming back in.
        RestoreTime();

        StartAnim(0f);
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
}