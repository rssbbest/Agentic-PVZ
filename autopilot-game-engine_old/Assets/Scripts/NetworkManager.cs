using System;
using System.Collections;
using System.Collections.Generic;
using System.Text;
using UnityEngine;
using UnityEngine.Events;
using UnityEngine.Networking;
using UnityEngine.SceneManagement;

public class NetworkManager : MonoBehaviour
{

    public static NetworkManager Instance { get; private set; }

    public string getTeamDataUrl = "http://127.0.0.1:5000/api/team";
    public string teamID;

    [Header("Scripts needed")]
    [SerializeField] UIController uicontroller;


    [Header("Push settings")]
    public string pushResultsUrl = "http://127.0.0.1:5000/api/results";
    public int maxAttempts = 3;
    public float retryDelaySeconds = 2f;
    public int timeoutSeconds = 10;


    // Raw JSON strings for your other scripts to parse
    public string DefenceJson { get; private set; }   // {"defencePlacementData":[...]}
    public string EnemiesJson { get; private set; }   // {"enemies":[...]}

    // Optional: fires once both strings are ready
    public event Action OnTeamDataReceived;



    private void Awake()
    {
        if (Instance != null && Instance != this)
        {
            Destroy(gameObject);
            return;
        }
        Instance = this;
        DontDestroyOnLoad(gameObject);
    }


    // Hook to the Submit button's OnClick
    public void SubmitTeamID(string teamId)
    {
        teamID = teamId;
        StartCoroutine(SendTeamID());
    }

    private IEnumerator SendTeamID()
    {
        GetUIController();
        if (teamID.Length < 4)
        {
            uicontroller.DisplayError("Enter valid team ID");
            yield return null;
        }
        string body = "{\"teamID\":\"" + teamID + "\"}";

        using (UnityWebRequest req = new UnityWebRequest(getTeamDataUrl, "POST"))
        {
            req.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(body));
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");

            yield return req.SendWebRequest();

            if (req.result == UnityWebRequest.Result.Success)
            {
                Debug.Log("Server JSON: " + req.downloadHandler.text);
                SplitTeamData(req.downloadHandler.text);
                
                //load next scene
                SceneManager.LoadScene(1);
            }
            else
            {
                Debug.LogError("Error: " + req.error + " | " + req.downloadHandler.text);
                uicontroller.DisplayError(GetErrorMessage(req));
            }
        }
    }




    void GetUIController()
    {
        if (uicontroller == null)
        {
            uicontroller = FindAnyObjectByType<UIController>();
        }
    }


    // Turns a failed request into a clean, human-readable string
    private string GetErrorMessage(UnityWebRequest req)
    {
        // No response from the server at all (server down, no internet, etc.)
        if (req.result == UnityWebRequest.Result.ConnectionError)
            return "Could not reach the server. Please check your connection.";

        // Server replied with JSON like {"error": "Team 'abc' not found"}
        string body = req.downloadHandler != null ? req.downloadHandler.text : "";
        if (!string.IsNullOrEmpty(body))
        {
            try
            {
                ErrorResponse err = JsonUtility.FromJson<ErrorResponse>(body);
                if (err != null && !string.IsNullOrEmpty(err.error))
                    return err.error;
            }
            catch (Exception)
            {
                // body wasn't JSON (e.g. an HTML error page), fall through
            }
        }

        // Fallback
        return "Request failed (" + req.responseCode + "): " + req.error;
    }

    // Call this from any script. onComplete is optional: (success, responseOrError)
    public void PushJson(string json, Action<bool, string> onComplete = null)
    {
        if (string.IsNullOrWhiteSpace(json))
        {
            string msg = "Nothing to send: JSON is empty.";
            uicontroller.DisplayError(msg);
            onComplete?.Invoke(false, msg);
            return;
        }
        StartCoroutine(PushJsonRoutine(json, onComplete));
    }

    private IEnumerator PushJsonRoutine(string json, Action<bool, string> onComplete)
    {
        byte[] bodyBytes = Encoding.UTF8.GetBytes(json);
        string lastError = "";

        for (int attempt = 1; attempt <= maxAttempts; attempt++)
        {
            // A UnityWebRequest can't be reused, so make a fresh one each attempt
            using (UnityWebRequest req = new UnityWebRequest(pushResultsUrl, "POST"))
            {
                req.uploadHandler = new UploadHandlerRaw(bodyBytes);
                req.downloadHandler = new DownloadHandlerBuffer();
                req.SetRequestHeader("Content-Type", "application/json");
                req.timeout = timeoutSeconds;

                yield return req.SendWebRequest();

                if (req.result == UnityWebRequest.Result.Success)
                {
                    Debug.Log("Push succeeded: " + req.downloadHandler.text);
                    onComplete?.Invoke(true, req.downloadHandler.text);
                    yield break;
                }

                lastError = GetErrorMessage(req);

                // Server answered with an error (400, 404, 500...): retrying won't help
                if (req.result != UnityWebRequest.Result.ConnectionError)
                {
                    Debug.LogError("Push rejected by server: " + lastError);
                    uicontroller.DisplayError(lastError);
                    onComplete?.Invoke(false, lastError);
                    yield break;
                }

                Debug.LogWarning($"Push attempt {attempt}/{maxAttempts} failed (network): {req.error}");
            }

            if (attempt < maxAttempts)
                yield return new WaitForSeconds(retryDelaySeconds);
        }

        // All attempts used up
        string finalError = $"Failed after {maxAttempts} attempts. {lastError}";
        Debug.LogError(finalError);
        uicontroller.DisplayError(finalError);
        onComplete?.Invoke(false, finalError);
    }


    private void SplitTeamData(string json)
    {
        try
        {
            TeamDataResponse full = JsonUtility.FromJson<TeamDataResponse>(json);

            DefenceJson = JsonUtility.ToJson(new DefenceWrapper
            {
                defencePlacementData = full.defencePlacementData ?? new List<DefencePlacement>()
            });

            EnemiesJson = JsonUtility.ToJson(new EnemyWrapper
            {
                enemies = full.enemies ?? new List<EnemySpawn>()
            });

            Debug.Log("DefenceJson: " + DefenceJson);
            Debug.Log("EnemiesJson: " + EnemiesJson);
            OnTeamDataReceived?.Invoke();
        }
        catch (Exception e)
        {
            Debug.LogError("Parse error: " + e.Message);
            uicontroller.DisplayError("Received invalid data from the server.");
        }
    }

}

[Serializable]
public class ErrorResponse
{
    public string error;
}


[Serializable] public class GridPosition { public int x; public int y; }
[Serializable] public class DefencePlacement { public int defenceId; public GridPosition defenceGridPosition; }
[Serializable] public class EnemySpawn { public int enemyId; public int count; }

// Used to read the full server response
[Serializable]
public class TeamDataResponse
{
    public List<DefencePlacement> defencePlacementData;
    public List<EnemySpawn> enemies;
}

// Used to produce the two separate strings
[Serializable] public class DefenceWrapper { public List<DefencePlacement> defencePlacementData; }
[Serializable] public class EnemyWrapper { public List<EnemySpawn> enemies; }