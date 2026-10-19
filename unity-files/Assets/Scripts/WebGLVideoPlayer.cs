
using UnityEngine;
using UnityEngine.Video;
using System.IO;

public class WebGLVideoPlayer : MonoBehaviour
{
    public VideoPlayer videoPlayer;
    public string videoFileName = "intro.mp4";

    void Start()
    {
        videoPlayer.source = VideoSource.Url;

        videoPlayer.url =
            Application.streamingAssetsPath + "/" + videoFileName;

        videoPlayer.errorReceived += OnVideoError;
        videoPlayer.prepareCompleted += OnPrepared;

        Debug.Log("Loading video: " + videoPlayer.url);

        videoPlayer.Prepare();
    }

    void OnPrepared(VideoPlayer player)
    {
        Debug.Log("Video prepared successfully!");
        player.Play();
    }

    void OnVideoError(VideoPlayer player, string message)
    {
        Debug.LogError("Video error: " + message);
    }
}
