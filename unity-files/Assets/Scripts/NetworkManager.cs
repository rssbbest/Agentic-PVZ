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
    public int timeoutSeconds = 30; // Allows the 15 s agent budget and referee overhead.

    // NEW: mid-game state capture endpoints (reuse retry settings above)
    [Header("Capture settings")]
    public string pushScreenshotUrl = "http://127.0.0.1:5000/api/screenshot";
    public string pushGameStateUrl = "http://127.0.0.1:5000/api/state";

    // NEW: mid-run reinforcement endpoint (reuses retry settings above)
    [Header("Reinforcement settings")]
    [SerializeField] string pushReinforcementsUrl = "http://127.0.0.1:5000/api/reinforcements";


    // Raw JSON strings for your other scripts to parse
    public string DefenceJson { get; private set; }   // {"defencePlacementData":[...]}
    public string EnemiesJson { get; private set; }   // {"enemies":[...]}
    // NEW: session id and raw level JSON (spawner reads "spawnSettings")
    public string SessionID { get; private set; }
    public string SpawnSettingsJson { get; private set; }
    // NEW: raw level JSON (GameManager reads "gameSettings")
    public string GameSettingsJson { get; private set; }
    // NEW: starting coin balance from team data
    public int Coins { get; private set; }

    // Optional: fires once both strings are ready
    public event Action OnTeamDataReceived;

    [Header("DEBUG MENU")]
    [SerializeField] bool DEBUG_MODE = false;

    bool teamRequestInFlight = false; // NEW: blocks a second Submit while the team request runs

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
        // NEW: ignore extra clicks while a request is running
        if (teamRequestInFlight)
        {
            return;
        }
        teamID = teamId;
        GetUIController();
        if (DEBUG_MODE)
        {
            getTeamDataUrl = uicontroller.DEBUG_url_team_data;
            pushResultsUrl = uicontroller.DEBUG_url_results;
            pushReinforcementsUrl = uicontroller.DEBUG_url_reinforcements;
        }
        StartCoroutine(SendTeamID());
    }

    private IEnumerator SendTeamID()
    {
        // UPDATED: stop on a bad ID (yield return null only waited a frame and sent anyway)
        if (string.IsNullOrWhiteSpace(teamID) || teamID.Trim().Length < 4)
        {
            ShowError("Enter valid team ID");
            yield break;
        }
        teamRequestInFlight = true; // NEW

        // UPDATED: serialized so quotes or backslashes in the ID can't break the JSON
        TeamIdRequest request = new TeamIdRequest();
        request.teamID = teamID;
        string body = JsonUtility.ToJson(request);

        using (UnityWebRequest req = new UnityWebRequest(getTeamDataUrl, "POST"))
        {
            req.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(body));
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");

            yield return req.SendWebRequest();
            teamRequestInFlight = false; // NEW: rest of this routine runs in one frame

            if (req.result == UnityWebRequest.Result.Success)
            {
                Debug.Log("Server JSON: " + req.downloadHandler.text);

                // UPDATED: only load Main when the team data parsed
                if (SplitTeamData(req.downloadHandler.text))
                {
                    //load next scene
                    SceneManager.LoadScene(1);
                }
            }
            else
            {
                Debug.LogError("Error: " + req.error + " | " + req.downloadHandler.text);
                ShowError(GetErrorMessage(req)); // UPDATED
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

    // NEW: menu error text only if the menu UI exists (it's destroyed once Main loads)
    void ShowError(string message)
    {
        GetUIController();
        if (uicontroller != null)
        {
            uicontroller.DisplayError(message);
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
            ShowError(msg); // UPDATED
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
                    ShowError(lastError); // UPDATED: menu UI is gone during Main
                    onComplete?.Invoke(false, lastError);
                    yield break;
                }

                Debug.LogWarning($"Push attempt {attempt}/{maxAttempts} failed (network): {req.error}");
            }

            if (attempt < maxAttempts)
                yield return new WaitForSecondsRealtime(retryDelaySeconds); // UPDATED: realtime, end screen runs at timeScale 0
        }

        // All attempts used up
        string finalError = $"Failed after {maxAttempts} attempts. {lastError}";
        Debug.LogError(finalError);
        ShowError(finalError); // UPDATED
        onComplete?.Invoke(false, finalError);
    }

    // NEW: screenshot upload as multipart/form-data (file + teamID + sessionID)
    public void PushScreenshot(byte[] jpgBytes, Action<bool, string> onComplete = null)
    {
        if (jpgBytes == null || jpgBytes.Length == 0)
        {
            string msg = "Nothing to send: screenshot is empty.";
            Debug.LogWarning(msg);
            onComplete?.Invoke(false, msg);
            return;
        }

        // multipart text sections can't be empty
        string teamValue = teamID;
        if (string.IsNullOrEmpty(teamValue))
        {
            teamValue = "Unknown";
        }

        List<IMultipartFormSection> sections = new List<IMultipartFormSection>();
        sections.Add(new MultipartFormFileSection("screenshot", jpgBytes, BuildScreenshotFileName(teamValue), "image/jpeg"));
        sections.Add(new MultipartFormDataSection("teamID", teamValue));
        sections.Add(new MultipartFormDataSection("sessionID", LoggingSystem.ResolveSessionId(SessionID)));

        // body built once so every retry sends identical bytes
        byte[] boundary = UnityWebRequest.GenerateBoundary();
        byte[] body = UnityWebRequest.SerializeFormSections(sections, boundary);
        string contentType = "multipart/form-data; boundary=" + Encoding.UTF8.GetString(boundary);

        StartCoroutine(PushCaptureRoutine(pushScreenshotUrl, body, contentType, "Screenshot", onComplete));
    }

    // NEW: state snapshot upload as JSON
    public void PushGameState(string json, Action<bool, string> onComplete = null)
    {
        if (string.IsNullOrWhiteSpace(json))
        {
            string msg = "Nothing to send: state snapshot is empty.";
            Debug.LogWarning(msg);
            onComplete?.Invoke(false, msg);
            return;
        }

        byte[] body = Encoding.UTF8.GetBytes(json);
        StartCoroutine(PushCaptureRoutine(pushGameStateUrl, body, "application/json", "State snapshot", onComplete));
    }

    // NEW: send state snapshot, response body (place/skip decision) goes to onComplete
    public void PushReinforcementRequest(string json, Action<bool, string> onComplete = null)
    {
        if (string.IsNullOrWhiteSpace(json))
        {
            string msg = "Nothing to send: reinforcement request is empty.";
            Debug.LogWarning(msg);
            onComplete?.Invoke(false, msg);
            return;
        }

        byte[] body = Encoding.UTF8.GetBytes(json);
        StartCoroutine(PushCaptureRoutine(pushReinforcementsUrl, body, "application/json", "Reinforcement request", onComplete));
    }

    // NEW: shared best-effort upload; never touches uicontroller (gone during gameplay)
    private IEnumerator PushCaptureRoutine(string url, byte[] body, string contentType, string label, Action<bool, string> onComplete)
    {
        string lastError = "";

        for (int attempt = 1; attempt <= maxAttempts; attempt++)
        {
            // A UnityWebRequest can't be reused, so make a fresh one each attempt
            using (UnityWebRequest req = new UnityWebRequest(url, "POST"))
            {
                req.uploadHandler = new UploadHandlerRaw(body);
                req.downloadHandler = new DownloadHandlerBuffer();
                req.SetRequestHeader("Content-Type", contentType);
                req.timeout = timeoutSeconds;

                yield return req.SendWebRequest();

                if (req.result == UnityWebRequest.Result.Success)
                {
                    Debug.Log(label + " push succeeded: " + req.downloadHandler.text);
                    onComplete?.Invoke(true, req.downloadHandler.text);
                    yield break;
                }

                lastError = GetErrorMessage(req);

                // Server answered with an error (400, 404, 500...): retrying won't help
                if (req.result != UnityWebRequest.Result.ConnectionError)
                {
                    Debug.LogError(label + " rejected by server: " + lastError);
                    onComplete?.Invoke(false, lastError);
                    yield break;
                }

                Debug.LogWarning($"{label} attempt {attempt}/{maxAttempts} failed (network): {req.error}");
            }

            if (attempt < maxAttempts)
            {
                // realtime, so retries still run while the game is paused or slowed
                yield return new WaitForSecondsRealtime(retryDelaySeconds);
            }
        }

        string finalError = $"{label} failed after {maxAttempts} attempts. {lastError}";
        Debug.LogError(finalError);
        onComplete?.Invoke(false, finalError);
    }

    // NEW: informational file name; backend should pick its own storage name
    private string BuildScreenshotFileName(string teamValue)
    {
        return $"screenshot_{teamValue}_{DateTime.Now:yyyyMMdd_HHmmss}.jpg";
    }


    private bool SplitTeamData(string json) // UPDATED: true = parsed, safe to load Main
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

            // NEW: session id + raw JSON for spawn settings
            SessionID = full.sessionID;
            SpawnSettingsJson = json;
            GameSettingsJson = json; // NEW: backend reinforcement settings
            Coins = full.coins; // NEW: starting coin balance

            OnTeamDataReceived?.Invoke();
            return true; // NEW
        }
        catch (Exception e)
        {
            Debug.LogError("Parse error: " + e.Message);
            ShowError("Received invalid data from the server."); // UPDATED
            return false; // NEW
        }
    }

}

[Serializable]
public class ErrorResponse
{
    public string error;
}

// NEW: request body for the team data call
[Serializable]
public class TeamIdRequest
{
    public string teamID;
}


[Serializable] public class GridPosition { public int x; public int y; }
[Serializable] public class DefencePlacement { public int defenceId; public GridPosition defenceGridPosition; }
[Serializable] public class EnemySpawn { public int enemyId; public int count; public List<int> allowedRows; } // UPDATED: allowedRows

// Used to read the full server response
[Serializable]
public class TeamDataResponse
{
    public string sessionID; // NEW
    public int coins; // NEW: starting coin balance
    public List<DefencePlacement> defencePlacementData;
    public List<EnemySpawn> enemies;
}

// Used to produce the two separate strings
[Serializable] public class DefenceWrapper { public List<DefencePlacement> defencePlacementData; }
[Serializable] public class EnemyWrapper { public List<EnemySpawn> enemies; }
