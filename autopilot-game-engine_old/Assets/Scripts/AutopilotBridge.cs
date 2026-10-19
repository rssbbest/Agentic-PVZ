using System;
using System.Collections;
using System.Collections.Generic;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.SceneManagement;

// The editor/player polls the organiser's local simulator queue. All combat
// remains in the original MonoBehaviours, prefabs, physics and animations.
public class AutopilotBridge : MonoBehaviour
{
    public static AutopilotBridge Instance { get; private set; }
    public static bool Managed => !string.IsNullOrEmpty(GatewayUrl);
    public static string GatewayUrl
    {
        get
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            // Web player is served by the same local integration service.
            if (Application.absoluteURL.Contains("autopilot=1"))
                return new Uri(Application.absoluteURL).GetLeftPart(UriPartial.Authority);
#endif
            string value = Environment.GetEnvironmentVariable("UNITY_GATEWAY_URL");
            string[] args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++)
                if (args[i] == "--autopilotGateway") value = args[i + 1];
            return value;
        }
    }
    int killed, escaped, total, placed, runId;
    float started;
    bool active;
    bool sendingFrame;
    float nextFrame;
    readonly List<EnemyAI> enemies = new List<EnemyAI>();

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
    static void Install()
    {
        if (!Managed) return;
        new GameObject("Autopilot HTTP Bridge").AddComponent<AutopilotBridge>();
    }

    void Awake()
    {
        if (Instance != null) { Destroy(gameObject); return; }
        Instance = this;
        DontDestroyOnLoad(gameObject);
    }

    IEnumerator Start()
    {
        while (true)
        {
            yield return null;
            string worker = Application.platform == RuntimePlatform.WebGLPlayer ? "web" : "native";
            using (UnityWebRequest req = UnityWebRequest.Get(GatewayUrl.TrimEnd('/') + "/unity/jobs/next?worker=" + worker))
            {
                req.timeout = 10;
                yield return req.SendWebRequest();
                if (req.result != UnityWebRequest.Result.Success)
                {
                    Debug.LogWarning("Autopilot queue connection unavailable");
                    yield return new WaitForSecondsRealtime(2);
                    continue;
                }
                Job job = JsonUtility.FromJson<Job>(req.downloadHandler.text);
                if (job == null || job.runId == 0)
                {
                    yield return new WaitForSecondsRealtime(.5f);
                    continue;
                }
                yield return Run(job);
            }
        }
    }

    IEnumerator Run(Job job)
    {
        runId = job.runId;
        LoggingSystem.Reset();
        killed = escaped = 0;
        total = 0;
        enemies.Clear();
        foreach (EnemySpawnData e in job.enemies) total += e.count;
        placed = job.defencePlacementData.Length;
        active = true;
        // Remove inspector demo units before applying this run's formation.
        foreach (DefenceBase dummy in FindObjectsByType<DefenceBase>(FindObjectsSortMode.None))
            Destroy(dummy.gameObject);
        foreach (EnemyAI dummy in FindObjectsByType<EnemyAI>(FindObjectsSortMode.None))
            Destroy(dummy.gameObject);
        yield return null;
        var placement = FindAnyObjectByType<PlacementSystem>();
        var spawning = FindAnyObjectByType<EnemySpawningHandler>();
        placement.ConvertDefencePlacementJsonToObject(JsonUtility.ToJson(job));
        placement.InitializeDefences();
        spawning.InitializeEnemies(JsonUtility.ToJson(job), true);
        spawning.StartSpawning();
        started = Time.time;
        nextFrame = Time.realtimeSinceStartup;
        float scale = 20f;
#if !UNITY_WEBGL || UNITY_EDITOR
        float.TryParse(Environment.GetEnvironmentVariable("UNITY_TIME_SCALE") ?? "20", out scale);
#endif
        Time.timeScale = Mathf.Clamp(scale, 1, 20);
        if (job.timeScale > 0) Time.timeScale = Mathf.Clamp(job.timeScale, 1, 20);
        while (killed + escaped < total && Time.time - started < 180f)
            yield return null;
        if (killed + escaped < total)
            escaped = total - killed; // unresolved enemies breach at round time limit
        int alive = 0;
        foreach (DefenceBase defence in FindObjectsByType<DefenceBase>(FindObjectsSortMode.None))
            if (defence.GetCurrentHealth() > 0) alive++;
        Result result = new Result {
            engine = Application.platform == RuntimePlatform.WebGLPlayer ? "unity-webgl" : "unity",
            totalEnemies = total, killed = killed, escaped = escaped,
            defencesPlaced = placed, defencesAlive = Mathf.Min(placed, alive),
            durationSeconds = Time.time - started, won = escaped == 0
        };
        active = false;
        string payload = JsonUtility.ToJson(new ResultWrapper { result = result });
        bool delivered = false;
        for (int attempt = 0; attempt < 4 && !delivered; attempt++)
        {
            using (UnityWebRequest req = new UnityWebRequest(GatewayUrl.TrimEnd('/') + "/unity/jobs/" + runId + "/result", "POST"))
            {
                req.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(payload));
                req.downloadHandler = new DownloadHandlerBuffer();
                req.SetRequestHeader("Content-Type", "application/json");
                req.timeout = 20;
                yield return req.SendWebRequest();
                delivered = req.result == UnityWebRequest.Result.Success;
            }
            if (!delivered) yield return new WaitForSecondsRealtime(1);
        }
        Debug.Log("AUTOPILOT_RESULT " + payload + " callback=" + delivered);
        Time.timeScale = 1;
        yield return SceneManager.LoadSceneAsync(SceneManager.GetActiveScene().buildIndex);
    }

    void Update()
    {
        if (active && !sendingFrame && Time.realtimeSinceStartup >= nextFrame)
        {
            nextFrame = Time.realtimeSinceStartup + .2f;
            StartCoroutine(PublishFrame());
        }
    }

    IEnumerator PublishFrame()
    {
        sendingFrame = true;
        var grid = FindAnyObjectByType<CustomGridSystem>();
        Vector3 origin = grid.GetCellFromNumbering(0, 0).transform.position;
        var es = new List<UnitFrame>();
        var ds = new List<UnitFrame>();
        for (int i = 0; i < enemies.Count; i++)
        {
            EnemyAI enemy = enemies[i];
            if (enemy == null || !enemy.gameObject.activeInHierarchy || enemy.GetCurrentHealth() <= 0) continue;
            es.Add(new UnitFrame { uid = i, kind = enemy.runEnemyKind,
                x = (enemy.transform.position.x - origin.x) / GridCell.nodeSize,
                y = Mathf.RoundToInt((enemy.transform.position.y - origin.y) / GridCell.nodeSize),
                hp = enemy.GetCurrentHealth(), maxHp = enemy.MaxHealth });
        }
        foreach (DefenceBase defence in FindObjectsByType<DefenceBase>(FindObjectsSortMode.None))
        {
            ds.Add(new UnitFrame { kind = defence.runDefenceKind,
                x = (defence.transform.position.x - origin.x) / GridCell.nodeSize,
                y = defence.myCell.y, hp = defence.GetCurrentHealth(), maxHp = defence.MaxHealth });
        }
        var frame = new Frame { time = Time.time - started, total = total,
            killed = killed, escaped = escaped, enemies = es.ToArray(), defences = ds.ToArray() };
        string payload = JsonUtility.ToJson(new FrameWrapper { snapshot = frame });
        using (UnityWebRequest req = new UnityWebRequest(GatewayUrl.TrimEnd('/') + "/unity/jobs/" + runId + "/frame", "POST"))
        {
            req.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(payload));
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");
            req.timeout = 3;
            yield return req.SendWebRequest();
        }
        sendingFrame = false;
    }

    void OnGUI()
    {
        if (active) GUI.Box(new Rect(10, 10, 360, 36),
            "Autopilot run " + runId + " | Killed " + killed + "/" + total + " | Breached " + escaped);
    }

    [Serializable] public class UnitFrame
    {
        public int uid, kind, y;
        public float x, hp, maxHp;
    }
    [Serializable] public class Frame
    {
        public float time;
        public int total, killed, escaped;
        public bool done;
        public UnitFrame[] enemies, defences;
    }
    [Serializable] public class FrameWrapper { public Frame snapshot; }

    public void Register(EnemyAI enemy) { if (active) enemies.Add(enemy); }
    public void EnemyResolved(bool dead) { if (active) { if (dead) killed++; else escaped++; } }

    [Serializable] public class Job
    {
        public int runId;
        public float timeScale;
        public EnemySpawnData[] enemies;
        public DefencePlacementCard[] defencePlacementData;
    }
    [Serializable] public class Result
    {
        public string engine;
        public int totalEnemies, killed, escaped, defencesPlaced, defencesAlive;
        public float durationSeconds;
        public bool won;
    }
    [Serializable] public class ResultWrapper { public Result result; }
}
