using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;




public static class LoggingSystem
{
    [Serializable]
    public class DefenceLog
    {
        public string id;
        public Vector2Int position;
        public float damageDealt;
        public bool destroyed;
        public float destroyedAtTime = -1f; // UPDATED: -1 = not destroyed
        public string type; // NEW: derived unit type
    }

    [Serializable]
    public class EnemyLog
    {
        public string id;
        public float damageDealt;
        public bool killed;
        public float killedAtTime = -1f; // UPDATED: -1 = not killed
        public string type; // NEW: derived unit type
    }

    [Serializable]
    class Report
    {
        public string teamID;
        public string sessionID = "Not Implemented Yet"; // NEW: session id placeholder
        public bool success;
        public float durationSeconds;
        public int enemiesKilled;
        public int defencesDestroyed;
        public float totalDefenceDamage;
        public float totalEnemyDamage;
        public List<DefenceLog> defences;
        public List<EnemyLog> enemies;
        // NEW: per-type stats
        public List<DefenceTypeStats> defenceTypeStats;
        public List<EnemyTypeStats> enemyTypeStats;
    }

    // NEW: per-type stats entries
    [Serializable]
    public class DefenceTypeStats
    {
        public string type;
        public int unitCount;
        public float totalDamageDealt;
        public int unitsDestroyed;
    }

    [Serializable]
    public class EnemyTypeStats
    {
        public string type;
        public int unitCount;
        public float totalDamageDealt;
        public int unitsKilled;
    }

    // NEW: live game state snapshot entries
    [Serializable]
    public class DefenceStateEntry
    {
        public string id;
        public string type;
        public float health;
        public float maxHealth;
        public Vector2Int position; // placement cell (myCell)
    }

    // NEW: snapshot order: by cell (column, then row), then id
    public static class SnapshotOrder
    {
        public static int Defences(DefenceStateEntry a, DefenceStateEntry b)
        {
            return ByCellThenId(a.position, a.id, b.position, b.id);
        }

        public static int Enemies(EnemyStateEntry a, EnemyStateEntry b)
        {
            return ByCellThenId(a.position, a.id, b.position, b.id);
        }

        static int ByCellThenId(Vector2Int pa, string ida, Vector2Int pb, string idb)
        {
            if (pa.x != pb.x) return pa.x.CompareTo(pb.x);
            if (pa.y != pb.y) return pa.y.CompareTo(pb.y);
            return string.CompareOrdinal(ida, idb);
        }
    }

    [Serializable]
    public class EnemyStateEntry
    {
        public string id;
        public string type;
        public float health;
        public float maxHealth;
        public Vector2Int position; // nearest grid cell, (-1,-1) if no grid
        public bool isAttacking;
        public bool isDead; // true while death animation plays
    }

    [Serializable]
    public class StateCounts
    {
        public int activeDefenceCount;
        public int activeEnemyCount;
        public int enemiesQueued;
        public int totalEnemiesInWave;
    }

    [Serializable]
    class StateSnapshot
    {
        public string teamID;
        public string sessionID;
        public float elapsedSeconds;
        public StateCounts counts;
        public List<DefenceStateEntry> defences;
        public List<EnemyStateEntry> enemies;
    }

    static readonly Dictionary<string, DefenceLog> defences = new Dictionary<string, DefenceLog>();
    static readonly Dictionary<string, EnemyLog> enemies = new Dictionary<string, EnemyLog>();
    static float startTime;

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
    public static void Reset()
    {
        defences.Clear();
        enemies.Clear();
        startTime = Time.time;
    }

    // ---- 1. Defence dealt damage this frame (call every frame it deals damage) ----
    public static void LogDefenceDamage(string id, Vector2Int position, float damage)
    {
        var d = GetDefence(id);
        d.position = position;
        d.damageDealt += damage;
    }

    // ---- 2. Enemy dealt damage this frame (call every frame it deals damage) ----
    public static void LogEnemyDamage(string id, float damage)
    {
        GetEnemy(id).damageDealt += damage;
    }

    // ---- 3. Defence destroyed ----
    // UPDATED: with its cell, so a defence that never dealt damage (a shield) is not
    // reported at (0, 0)
    public static void LogDefenceDestroyed(string id, Vector2Int position)
    {
        var d = GetDefence(id);
        d.position = position;
        d.destroyed = true;
        d.destroyedAtTime = Time.time - startTime;
    }

    // ---- 4. Enemy killed ----
    public static void LogEnemyKilled(string id)
    {
        var e = GetEnemy(id);
        e.killed = true;
        e.killedAtTime = Time.time - startTime;
    }

    // ---- Compile everything logged so far into JSON (also saved to disk) ----
    public static string Compile(bool success, string teamId, bool saveToFile = true, string sessionId = null) // UPDATED: sessionId
    {
        var r = new Report
        {
            teamID = teamId,
            sessionID = ResolveSessionId(sessionId), // NEW
            success = success,
            durationSeconds = Time.time - startTime,
            defences = new List<DefenceLog>(defences.Values),
            enemies = new List<EnemyLog>(enemies.Values)
        };
        foreach (var d in r.defences) { r.totalDefenceDamage += d.damageDealt; if (d.destroyed) r.defencesDestroyed++; }
        foreach (var e in r.enemies) { r.totalEnemyDamage += e.damageDealt; if (e.killed) r.enemiesKilled++; }
        // NEW: per-type stats, sorted by type
        r.defenceTypeStats = BuildDefenceTypeStats(r.defences);
        r.enemyTypeStats = BuildEnemyTypeStats(r.enemies);

        string json = JsonUtility.ToJson(r, true);
        if (saveToFile)
        {
            string path = Path.Combine(Application.persistentDataPath,
                $"battlelog_{DateTime.Now:yyyyMMdd_HHmmss}.json");
            File.WriteAllText(path, json);
            Debug.Log("Battle log saved: " + path);
        }
        return json;
    }

    // NEW: backend session id, blank falls back to placeholder
    public const string DefaultSessionId = "Not Implemented Yet";
    public static string ResolveSessionId(string sessionId)
    {
        if (string.IsNullOrWhiteSpace(sessionId))
        {
            return DefaultSessionId;
        }
        return sessionId;
    }

    // NEW: seconds since Reset(), same clock as Report.durationSeconds
    public static float ElapsedSeconds()
    {
        return Time.time - startTime;
    }

    // NEW: live state of active units on screen as JSON (no file save by default, WebGL)
    public static string CaptureStateSnapshot(string teamId, string sessionId, bool saveToFile = false)
    {
        CustomGridSystem grid = UnityEngine.Object.FindAnyObjectByType<CustomGridSystem>();
        if (grid == null)
        {
            Debug.LogWarning("[Snapshot] No CustomGridSystem found, enemy positions will be (-1,-1)");
        }

        List<DefenceStateEntry> defenceStates = CollectDefenceStates();
        List<EnemyStateEntry> enemyStates = CollectEnemyStates(grid);
        StateCounts counts = CollectCounts();

        string json = BuildStateSnapshotJson(teamId, sessionId, ElapsedSeconds(), counts, defenceStates, enemyStates);

        if (saveToFile)
        {
            string path = Path.Combine(Application.persistentDataPath,
                $"statesnapshot_{DateTime.Now:yyyyMMdd_HHmmss}.json");
            File.WriteAllText(path, json);
            Debug.Log("State snapshot saved: " + path);
        }
        Debug.Log($"result json are -  {json}");
        return json;
    }

    // NEW: pure JSON builder, no scene / clock / disk access
    public static string BuildStateSnapshotJson(string teamId, string sessionId, float elapsedSeconds,
                                                StateCounts counts, List<DefenceStateEntry> defenceStates,
                                                List<EnemyStateEntry> enemyStates, bool prettyPrint = true)
    {
        StateSnapshot snapshot = new StateSnapshot();
        snapshot.teamID = teamId;
        snapshot.sessionID = ResolveSessionId(sessionId);
        snapshot.elapsedSeconds = elapsedSeconds;

        if (defenceStates != null)
        {
            snapshot.defences = defenceStates;
        }
        else
        {
            snapshot.defences = new List<DefenceStateEntry>();
        }

        if (enemyStates != null)
        {
            snapshot.enemies = enemyStates;
        }
        else
        {
            snapshot.enemies = new List<EnemyStateEntry>();
        }

        // counts always agree with the arrays
        StateCounts resolved = new StateCounts();
        if (counts != null)
        {
            resolved.enemiesQueued = counts.enemiesQueued;
            resolved.totalEnemiesInWave = counts.totalEnemiesInWave;
        }
        resolved.activeDefenceCount = snapshot.defences.Count;
        resolved.activeEnemyCount = snapshot.enemies.Count;
        snapshot.counts = resolved;

        return JsonUtility.ToJson(snapshot, prettyPrint);
    }

    // NEW: active defences only (inactive objects are excluded)
    static List<DefenceStateEntry> CollectDefenceStates()
    {
        List<DefenceStateEntry> list = new List<DefenceStateEntry>();
        DefenceBase[] found = UnityEngine.Object.FindObjectsByType<DefenceBase>(FindObjectsSortMode.None);
        foreach (DefenceBase defence in found)
        {
            DefenceStateEntry entry = new DefenceStateEntry();
            entry.id = defence.name;
            entry.type = DeriveType(defence.name);
            entry.health = defence.GetCurrentHealth();
            entry.maxHealth = defence.GetMaxHealth();
            entry.position = defence.myCell;
            list.Add(entry);
        }
        // UPDATED: a fixed order (Find returns objects unordered), so the same game
        // state is always the same JSON and the agent sees the same prompt
        list.Sort(SnapshotOrder.Defences);
        return list;
    }

    // NEW: active enemies only; queued enemies are inactive so they are skipped
    static List<EnemyStateEntry> CollectEnemyStates(CustomGridSystem grid)
    {
        List<EnemyStateEntry> list = new List<EnemyStateEntry>();
        EnemyAI[] found = UnityEngine.Object.FindObjectsByType<EnemyAI>(FindObjectsSortMode.None);
        foreach (EnemyAI enemy in found)
        {
            EnemyStateEntry entry = new EnemyStateEntry();
            entry.id = enemy.name;
            entry.type = DeriveType(enemy.name);
            entry.health = enemy.GetCurrentHealth();
            entry.maxHealth = enemy.GetMaxHealth();
            if (grid != null)
            {
                entry.position = grid.GetNearestCellNumbering(enemy.transform.position);
            }
            else
            {
                entry.position = new Vector2Int(-1, -1);
            }
            entry.isAttacking = enemy.GetCurrentAttackState();
            entry.isDead = enemy.IsDead();
            list.Add(entry);
        }
        list.Sort(SnapshotOrder.Enemies); // UPDATED: fixed order, as for defences
        return list;
    }

    // NEW: wave progress from the spawner, zeros if none
    static StateCounts CollectCounts()
    {
        StateCounts counts = new StateCounts();
        EnemySpawningHandler spawner = UnityEngine.Object.FindAnyObjectByType<EnemySpawningHandler>();
        if (spawner != null)
        {
            counts.enemiesQueued = spawner.EnemyCountInSpawnQueue();
            counts.totalEnemiesInWave = spawner.TotalEnemiesInWave();
        }
        return counts;
    }

    static DefenceLog GetDefence(string id)
    {
        // UPDATED: new entries also store their type
        DefenceLog d;
        if (!defences.TryGetValue(id, out d))
        {
            d = new DefenceLog { id = id, type = DeriveType(id) };
            defences[id] = d;
        }
        return d;
    }

    static EnemyLog GetEnemy(string id)
    {
        // UPDATED: new entries also store their type
        EnemyLog e;
        if (!enemies.TryGetValue(id, out e))
        {
            e = new EnemyLog { id = id, type = DeriveType(id) };
            enemies[id] = e;
        }
        return e;
    }

    // NEW: unit type from id, e.g. "EnemClown_3" / "EnemClown(Clone)" -> "EnemClown"
    public static string DeriveType(string id)
    {
        if (string.IsNullOrEmpty(id))
        {
            return "Unknown";
        }

        string type = id;
        bool changed = true;
        while (changed && type.Length > 0)
        {
            changed = false;

            // strip trailing "(Clone)"
            if (type.EndsWith("(Clone)", StringComparison.Ordinal))
            {
                type = type.Substring(0, type.Length - "(Clone)".Length).TrimEnd();
                changed = true;
            }

            // strip trailing "_<digits>"
            int i = type.Length;
            while (i > 0 && char.IsDigit(type[i - 1]))
            {
                i--;
            }
            if (i > 0 && i < type.Length && type[i - 1] == '_')
            {
                type = type.Substring(0, i - 1);
                changed = true;
            }
        }

        if (type.Length == 0)
        {
            return "Unknown";
        }
        return type;
    }

    // NEW: group defence entries by type
    static List<DefenceTypeStats> BuildDefenceTypeStats(List<DefenceLog> logs)
    {
        Dictionary<string, DefenceTypeStats> map = new Dictionary<string, DefenceTypeStats>();
        foreach (DefenceLog d in logs)
        {
            DefenceTypeStats s;
            if (!map.TryGetValue(d.type, out s))
            {
                s = new DefenceTypeStats { type = d.type };
                map[d.type] = s;
            }
            s.unitCount++;
            s.totalDamageDealt += d.damageDealt;
            if (d.destroyed)
            {
                s.unitsDestroyed++;
            }
        }

        // sorted by type name
        List<string> types = new List<string>(map.Keys);
        types.Sort(string.CompareOrdinal);
        List<DefenceTypeStats> list = new List<DefenceTypeStats>();
        foreach (string type in types)
        {
            list.Add(map[type]);
        }
        return list;
    }

    // NEW: group enemy entries by type
    static List<EnemyTypeStats> BuildEnemyTypeStats(List<EnemyLog> logs)
    {
        Dictionary<string, EnemyTypeStats> map = new Dictionary<string, EnemyTypeStats>();
        foreach (EnemyLog e in logs)
        {
            EnemyTypeStats s;
            if (!map.TryGetValue(e.type, out s))
            {
                s = new EnemyTypeStats { type = e.type };
                map[e.type] = s;
            }
            s.unitCount++;
            s.totalDamageDealt += e.damageDealt;
            if (e.killed)
            {
                s.unitsKilled++;
            }
        }

        // sorted by type name
        List<string> types = new List<string>(map.Keys);
        types.Sort(string.CompareOrdinal);
        List<EnemyTypeStats> list = new List<EnemyTypeStats>();
        foreach (string type in types)
        {
            list.Add(map[type]);
        }
        return list;
    }
}

/* USAGE (UPDATED)
   Ids are unit names. Spawners rename units "<PrefabName>_<n>" (e.g. "EnemClown_0"); type = DeriveType(id).
   LoggingSystem.Reset();                                   // GameManager.Start, once per simulation
   LoggingSystem.LogDefenceDamage(name, myCell, dmg);       // myCell = Vector2Int grid cell
   LoggingSystem.LogEnemyDamage(name, dmg * Time.deltaTime);
   LoggingSystem.LogDefenceDestroyed(name, myCell);
   LoggingSystem.LogEnemyKilled(name);
   string json = LoggingSystem.Compile(success, teamId, saveToFile: true); // includes defenceTypeStats / enemyTypeStats
*/







