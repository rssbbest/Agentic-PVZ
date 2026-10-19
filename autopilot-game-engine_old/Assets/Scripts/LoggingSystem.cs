using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.CompilerServices;
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
        public float destroyedAtTime;
    }

    [Serializable]
    public class EnemyLog
    {
        public string id;
        public float damageDealt;
        public bool killed;
        public float killedAtTime;
    }

    [Serializable]
    class Report
    {
        public string teamID;
        public bool success;
        public float durationSeconds;
        public int enemiesKilled;
        public int defencesDestroyed;
        public float totalDefenceDamage;
        public float totalEnemyDamage;
        public List<DefenceLog> defences;
        public List<EnemyLog> enemies;
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
    public static void LogDefenceDestroyed(string id)
    {
        var d = GetDefence(id);
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
    public static string Compile(bool success, string teamId, bool saveToFile = true)
    {
        var r = new Report
        {
            teamID = teamId,
            success = success,
            durationSeconds = Time.time - startTime,
            defences = new List<DefenceLog>(defences.Values),
            enemies = new List<EnemyLog>(enemies.Values)
        };
        foreach (var d in r.defences) { r.totalDefenceDamage += d.damageDealt; if (d.destroyed) r.defencesDestroyed++; }
        foreach (var e in r.enemies) { r.totalEnemyDamage += e.damageDealt; if (e.killed) r.enemiesKilled++; }

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

    static DefenceLog GetDefence(string id)
    {
        if (!defences.TryGetValue(id, out var d)) defences[id] = d = new DefenceLog { id = id };
        return d;
    }

    static EnemyLog GetEnemy(string id)
    {
        if (!enemies.TryGetValue(id, out var e)) enemies[id] = e = new EnemyLog { id = id };
        return e;
    }
}

/* USAGE
   BattleLogger.LogDefenceDamage("Turret_A", transform.position, dmg * Time.deltaTime);
   BattleLogger.LogEnemyDamage("Enemy_12", dmg * Time.deltaTime);
   BattleLogger.LogDefenceDestroyed("Turret_A");
   BattleLogger.LogEnemyKilled("Enemy_12");
   string json = BattleLogger.Compile(success: true);
*/







