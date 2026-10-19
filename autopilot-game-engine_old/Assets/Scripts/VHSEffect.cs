using UnityEngine;
using UnityEngine.UI;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

public class VHSEffect : MonoBehaviour
{
    [SerializeField] Image overlay;                 // Image using the VHS material (can be disabled in the scene)
    [SerializeField] VolumeProfile defaultProfile;  // drag DefaultVolumeProfile.asset here
    [SerializeField] float maxAberration = 1f;
    [SerializeField] float fadeInTime = 0.15f;
    [SerializeField] float fadeOutTime = 0.15f;

    Material mat;
    VolumeProfile runtimeProfile;
    ChromaticAberration ca;
    FilmGrain grain;

    bool active;
    float envelope;   // 0 = effect off, 1 = effect fully on

    void Awake()
    {
        mat = Instantiate(overlay.material);
        overlay.material = mat;

        // Runtime copy of the default profile, so the asset itself is never modified
        runtimeProfile = Instantiate(defaultProfile);

        if (!runtimeProfile.TryGet(out ca))
            ca = runtimeProfile.Add<ChromaticAberration>(true);
        if (!runtimeProfile.TryGet(out grain))
            grain = runtimeProfile.Add<FilmGrain>(true);

        ca.intensity.overrideState = true;
        grain.intensity.overrideState = true;
        grain.type.Override(FilmGrainLookup.Medium1);

        // Temporary global volume that sits above the default one
        var go = new GameObject("VHS Runtime Volume");
        go.transform.SetParent(transform);
        var vol = go.AddComponent<Volume>();
        vol.isGlobal = true;
        vol.priority = 100;
        vol.sharedProfile = runtimeProfile;

        SetAmount(0);
        SetOverlayVisible(false);
    }

    void OnDestroy()
    {
        if (runtimeProfile) Destroy(runtimeProfile);
    }

    void Update()
    {
        // Nothing to do while fully off
        if (!active && envelope <= 0f) return;

        // Unscaled so it still animates when Time.timeScale = 0
        mat.SetFloat("_T", Time.unscaledTime);

        float fade = active ? fadeInTime : fadeOutTime;
        float target = active ? 1f : 0f;
        envelope = Mathf.MoveTowards(envelope, target, Time.unscaledDeltaTime / Mathf.Max(fade, 0.0001f));

        if (!active && envelope <= 0f)
        {
            SetAmount(0);
            SetOverlayVisible(false);
            return;
        }

        float jitter = Random.Range(0.7f, 1f);
        SetAmount(envelope * jitter);
    }

    /// <summary>Starts the effect and keeps it running until Deactivate() is called.
    /// rewind = false for fast forward, true for rewind.</summary>
    public void Activate(bool rewind = false)
    {
        mat.SetFloat("_Dir", rewind ? -1f : 1f);
        SetOverlayVisible(true);   // enables the Image even if it was disabled
        active = true;
    }

    /// <summary>Fades the effect out and hides the Image.</summary>
    public void Deactivate()
    {
        active = false;
    }

    void SetOverlayVisible(bool visible)
    {
        if (visible && !overlay.gameObject.activeSelf)
            overlay.gameObject.SetActive(true);
        overlay.enabled = visible;
    }

    void SetAmount(float a)
    {
        mat.SetFloat("_Intensity", a);
        ca.intensity.value = a * maxAberration;
        grain.intensity.value = a;
    }
}