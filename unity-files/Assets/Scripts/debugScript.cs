using UnityEngine;
using UnityEngine.Video;

public class debugScript : MonoBehaviour
{
    VideoPlayer video;
    void Awake()
    {
        
        video = GetComponent<VideoPlayer>();

        video.errorReceived += OnError;
        video.prepareCompleted += OnPrepared;
        video.started += OnStarted;
        video.loopPointReached += OnFinished;

        Debug.Log("VideoPlayer found");
        Debug.Log("Clip: " + video.clip);
        Debug.Log("Source: " + video.source);
        Debug.Log("Render Mode: " + video.renderMode);
    }

    void Start()
    {
        Debug.Log("Preparing video...");
        video.Prepare();
        
    }

    void OnPrepared(VideoPlayer vp)
    {
        Debug.Log("VIDEO PREPARED");
        Debug.Log($"Resolution: {vp.width}x{vp.height}");
        Debug.Log($"Length: {vp.length}");
        Debug.Log($"Frame Count: {vp.frameCount}");

        vp.Play();
    }

    void OnStarted(VideoPlayer vp)
    {
        Debug.Log("VIDEO STARTED");
    }

    void OnError(VideoPlayer vp, string message)
    {
        Debug.LogError("VIDEO ERROR: " + message);
    }

    void OnFinished(VideoPlayer vp)
    {
        Debug.Log("VIDEO FINISHED");
    }

}
