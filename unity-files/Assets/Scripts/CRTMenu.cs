using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;
using UnityEngine.UI;

public class CRTMenu : MonoBehaviour
{
    [Header("References")]
    public Camera gameCamera;
    [Tooltip("Full-screen RawImage on a Screen Space - Overlay canvas. May start disabled.")]
    public RawImage overlay;
    [Tooltip("Material using the UI/CRTPauseOverlay shader.")]
    public Material crtMaterial;

    [Header("TV Frame (optional)")]
    [Tooltip("Root of the TV frame UI (needs a CanvasGroup). Built by Tools > CRT > Build TV Frame. May start disabled.")]
    public CanvasGroup frameGroup;
    [Tooltip("OPTIONAL. Invisible RectTransform marking the glass window. Leave EMPTY to set the screen area with the material's Screen Size / Screen Offset instead.")]
    public RectTransform glassRect;
    [Tooltip("The frame starts fading in once the effect passes this point (0-1).")]
    [Range(0f, 0.95f)] public float frameFadeStart = 0.55f;
    [Tooltip("The frame starts slightly bigger and settles to 1.")]
    [Range(1f, 1.3f)] public float frameIntroScale = 1.06f;

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
    [Tooltip("Copies CRT_Material's values every frame, so tweaks made to the material while playing show up right away.")]
    public bool liveMaterialEdit = true;

    [Header("Post-processing (temporary, never touches DefaultVolumeProfile)")]
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
    float amount;                 // 0 = normal game, 1 = fully in the TV
    bool rendering;
    float nextRenderTime;
    Coroutine anim;
    readonly Vector3[] corners = new Vector3[4];

    Camera screenCamera;          // keeps something rendering to the screen (no "No cameras rendering")
    Volume volume;
    VolumeProfile volumeProfile;

    // saved state, restored when the effect ends
    bool stateSaved;
    bool camWasEnabled, overlayWasEnabled, canvasWasEnabled;
    float frameAlphaWas = 1f;
    Vector3 frameScaleWas = Vector3.one;
    Canvas overlayCanvas;
    readonly List<GameObject> activatedObjects = new List<GameObject>();
    bool timeFrozen;
    float savedTimeScale = 1f;

    // ------------------------------------------------------------------ public API

    /// <summary>Enable the overlay + frame and start the transition into the CRT TV.</summary>
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

    /// <summary>Reverse the transition, then restore camera, overlay, frame, volume and time scale.</summary>
    [ContextMenu("Deactivate Effect")]
    public void DeactivateEffect()
    {
        if (!IsEffectActive) return;
        IsEffectActive = false;

        RestoreTime();
        StartAnim(0f);
    }

    // ------------------------------------------------------------------ unity

    void Awake()
    {
        if (!gameCamera) gameCamera = Camera.main;

        mat = new Material(crtMaterial);
        overlay.material = mat;
        overlay.raycastTarget = false;

        if (useVolumeEffects) BuildVolume();
        BuildScreenCamera();
    }

    void Update()
    {
        if (!rendering) return;

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
        SaveStateAndEnableUI();          // overlay + frame are enabled BEFORE anything else happens

        int h = Mathf.Max(64, renderHeight);
        int w = Mathf.RoundToInt(h * (float)Screen.width / Screen.height);
        if (rt == null || rt.width != w || rt.height != h)
        {
            if (rt) { rt.Release(); Destroy(rt); }
            rt = new RenderTexture(w, h, 24) { name = "CRT_Capture", filterMode = filterMode, wrapMode = TextureWrapMode.Clamp };
            rt.Create();
        }

        gameCamera.enabled = false;      // rendered manually at displayFps
        gameCamera.targetTexture = rt;

        screenCamera.targetDisplay = gameCamera.targetDisplay;
        screenCamera.enabled = true;

        if (volume)
        {
            volume.weight = amount;
            volume.enabled = true;
        }

        Canvas.ForceUpdateCanvases();    // make sure the frame's layout is up to date before we read the glass rect
        gameCamera.Render();
        overlay.texture = rt;
        ApplyProperties();

        rendering = true;
        nextRenderTime = Time.unscaledTime + (displayFps > 0f ? 1f / displayFps : 0f);
    }

    void SaveStateAndEnableUI()
    {
        if (stateSaved) return;
        stateSaved = true;

        camWasEnabled = gameCamera.enabled;
        overlayWasEnabled = overlay.enabled;

        activatedObjects.Clear();
        EnableWithParents(overlay.transform);

        overlayCanvas = overlay.GetComponentInParent<Canvas>(true);
        if (overlayCanvas)
        {
            canvasWasEnabled = overlayCanvas.enabled;
            overlayCanvas.enabled = true;
        }

        overlay.enabled = true;

        if (frameGroup)
        {
            EnableWithParents(frameGroup.transform);
            frameAlphaWas = frameGroup.alpha;
            frameScaleWas = frameGroup.transform.localScale;
            frameGroup.alpha = 0f;
        }
    }

    void EnableWithParents(Transform start)
    {
        for (Transform t = start; t != null; t = t.parent)
        {
            if (!t.gameObject.activeSelf)
            {
                t.gameObject.SetActive(true);
                activatedObjects.Add(t.gameObject);
            }
        }
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

        if (frameGroup)
        {
            frameGroup.alpha = frameAlphaWas;
            frameGroup.transform.localScale = frameScaleWas;
        }

        if (overlay)
        {
            overlay.enabled = overlayWasEnabled;
            if (overlayCanvas) overlayCanvas.enabled = canvasWasEnabled;
        }
        foreach (var go in activatedObjects)
            if (go) go.SetActive(false);   // back to how they were in the scene
        activatedObjects.Clear();
        stateSaved = false;
    }

    void RestoreTime()
    {
        if (!timeFrozen) return;
        Time.timeScale = savedTimeScale;
        timeFrozen = false;
    }

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

    // Runtime-only Volume: blends on top of your DefaultVolumeProfile and is switched off afterwards.
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

        if (target <= 0f) EndEffect();
    }

    // ------------------------------------------------------------------ per-frame shader values

    void ApplyProperties()
    {
        // pick up edits made to the CRT_Material asset while playing
        if (liveMaterialEdit && crtMaterial) mat.CopyPropertiesFromMaterial(crtMaterial);

        float aspect = (float)Screen.width / Screen.height;

        // frame fades/settles in during the last part of the transition
        if (frameGroup)
        {
            float f = Mathf.SmoothStep(0f, 1f, Mathf.InverseLerp(frameFadeStart, 1f, amount));
            frameGroup.alpha = f;
            frameGroup.transform.localScale = frameScaleWas * Mathf.Lerp(frameIntroScale, 1f, f);
        }

        // Screen area: from the frame's GlassRect if assigned, otherwise from the material (Screen Size / Offset)
        bool useRect = glassRect != null;
        if (useRect)
        {
            Canvas root = glassRect.GetComponentInParent<Canvas>();
            if (root) root = root.rootCanvas;
            Camera cam = (root && root.renderMode != RenderMode.ScreenSpaceOverlay) ? root.worldCamera : null;

            glassRect.GetWorldCorners(corners);
            Vector2 a = RectTransformUtility.WorldToScreenPoint(cam, corners[0]);
            Vector2 b = RectTransformUtility.WorldToScreenPoint(cam, corners[2]);

            float unit = Screen.height * 0.5f;          // view units: screen height = 2
            Vector2 mid = (a + b) * 0.5f - new Vector2(Screen.width, Screen.height) * 0.5f;
            mat.SetVector("_GlassCenter", mid / unit);
            mat.SetVector("_GlassHalf", new Vector2(Mathf.Abs(b.x - a.x), Mathf.Abs(b.y - a.y)) * 0.5f / unit);
        }
        mat.SetFloat("_UseGlassRect", useRect ? 1f : 0f);

        mat.SetFloat("_Amount", amount);
        mat.SetFloat("_Aspect", aspect);
        mat.SetFloat("_ContentAspect", rt ? (float)rt.width / rt.height : aspect);
        mat.SetFloat("_UTime", Time.unscaledTime);
        if (rt) mat.SetVector("_PixelSize", new Vector4(1f / rt.width, 1f / rt.height, 0, 0));

        if (volume) volume.weight = Mathf.Clamp01(amount);
    }


}
