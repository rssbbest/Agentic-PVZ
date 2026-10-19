using System.Collections.Generic;
using UnityEngine;

// NEW: spawn timing sent by backend under "spawnSettings"
[System.Serializable]
public class SpawnSettings
{
    public float interArrivalTime;
    public float timeDecreaseRate;
    public float minInterArrivalTime;
    public int[] spawnSequence; // NEW: enemyIds in spawn order, null/empty = random
    public int seed; // NEW: spawn rows and order come from this seed: same in every game (0 = random)

    public SpawnSettings Copy()
    {
        SpawnSettings copy = new SpawnSettings();
        copy.interArrivalTime = interArrivalTime;
        copy.timeDecreaseRate = timeDecreaseRate;
        copy.minInterArrivalTime = minInterArrivalTime;
        copy.spawnSequence = spawnSequence; // NEW
        copy.seed = seed; // NEW
        return copy;
    }
}

// NEW: mirrors the root key of the level JSON
[System.Serializable]
public class SpawnSettingsEnvelope
{
    public SpawnSettings spawnSettings;
}

// NEW: one fixed simulation step per frame, so a level plays out the same on every
// machine and frame rate (a slow machine plays slower, never differently).
public static class SimClock
{
    public const float Step = 1f / 60f;

    public static void Fix()
    {
        Time.captureDeltaTime = Step;
        Application.targetFrameRate = 60;
        // NEW: 2D physics (projectile hits) steps once per frame on this clock; on its
        // own 50 Hz clock a hit could land a frame early or late depending on how long
        // the menu ran, and the same agent would get a slightly different game
        Physics2D.simulationMode = SimulationMode2D.Update;
    }

    // frames that make up `seconds` of real time, used where game time keeps moving
    public static int Frames(float seconds)
    {
        return Mathf.Max(1, Mathf.CeilToInt(seconds / Step));
    }
}

// NEW: pure spawn rules (rows, wait floor, settings overlay)
public static class SpawnRules
{
    public const string RowsWarningPrefix = "[Spawn] ";

    // valid rows in 0..maxRow, or null = all rows
    public static List<int> ResolveRows(List<int> allowedRows, int maxRow, string label)
    {
        if (allowedRows == null || allowedRows.Count == 0)
        {
            return null;
        }

        List<int> result = new List<int>();
        foreach (int row in allowedRows)
        {
            if (row >= 0 && row <= maxRow)
            {
                result.Add(row);
            }
            else
            {
                Debug.LogWarning($"{RowsWarningPrefix}{label}: row {row} outside 0..{maxRow}, ignored");
            }
        }

        if (result.Count == 0)
        {
            Debug.LogWarning($"{RowsWarningPrefix}{label}: no valid allowedRows, using all rows");
            return null;
        }
        return result;
    }

    // null = any row (same random call as before)
    public static int PickRow(List<int> validRows, int maxRow)
    {
        return PickRow(validRows, maxRow, null);
    }

    // NEW: rng = the level's seeded generator (same rows every game), null = Unity's random
    public static int PickRow(List<int> validRows, int maxRow, System.Random rng)
    {
        int count = validRows == null ? maxRow + 1 : validRows.Count;
        int index = Pick(count, rng);
        return validRows == null ? index : validRows[index];
    }

    // NEW: an index in 0..count-1 from the seeded generator, or Unity's random without one
    public static int Pick(int count, System.Random rng)
    {
        return rng != null ? rng.Next(count) : UnityEngine.Random.Range(0, count);
    }

    // NEW: the level's generator. A seed (the hidden test cases) spawns the same way in
    // every game, so the same agent gets the same score; no seed (practice and the public
    // test cases) is null: Unity's random rows and order, as in build10.
    public static System.Random SeededRandom(int seed)
    {
        return seed != 0 ? new System.Random(seed) : null;
    }

    // wait never drops below the floor
    public static float NextWait(float timeLeft, float minInterArrivalTime)
    {
        return Mathf.Max(timeLeft, minInterArrivalTime);
    }

    // fields missing from JSON keep their default value
    public static SpawnSettings ParseSpawnSettings(string levelJson, SpawnSettings defaults)
    {
        if (string.IsNullOrWhiteSpace(levelJson))
        {
            return defaults.Copy();
        }

        SpawnSettingsEnvelope envelope = new SpawnSettingsEnvelope();
        envelope.spawnSettings = defaults.Copy();
        try
        {
            JsonUtility.FromJsonOverwrite(levelJson, envelope);
        }
        catch (System.Exception e)
        {
            Debug.LogWarning(RowsWarningPrefix + "invalid spawnSettings JSON, using inspector values: " + e.Message);
            return defaults.Copy();
        }

        if (envelope.spawnSettings == null)
        {
            return defaults.Copy();
        }
        return envelope.spawnSettings;
    }
}
