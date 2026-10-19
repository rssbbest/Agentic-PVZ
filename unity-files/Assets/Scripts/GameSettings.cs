using System.Text.RegularExpressions;
using UnityEngine;

// NEW: reinforcement settings sent by backend under "gameSettings" (names match GameManager fields)
[System.Serializable]
public class GameSettings
{
    public bool reinforcementsEnabled;
    public float reinforcementIntervalSeconds;
    public float reinforcementHoldSeconds;
    public bool sendScreenshotToo;

    public GameSettings Copy()
    {
        GameSettings copy = new GameSettings();
        copy.reinforcementsEnabled = reinforcementsEnabled;
        copy.reinforcementIntervalSeconds = reinforcementIntervalSeconds;
        copy.reinforcementHoldSeconds = reinforcementHoldSeconds;
        copy.sendScreenshotToo = sendScreenshotToo;
        return copy;
    }
}

// NEW: mirrors the root key of the level JSON
[System.Serializable]
public class GameSettingsEnvelope
{
    public GameSettings gameSettings;
}

// NEW: pure settings overlay, same rules as spawnSettings
public static class GameSettingsRules
{
    public const string LogPrefix = "[GameSettings] ";

    // explicit "gameSettings": null, which JsonUtility can't express as a missing object
    static readonly Regex NullGameSettings = new Regex("\"gameSettings\"\\s*:\\s*null");

    // fields missing from JSON keep their default (inspector) value
    public static GameSettings ParseGameSettings(string levelJson, GameSettings defaults)
    {
        if (string.IsNullOrWhiteSpace(levelJson))
        {
            return defaults.Copy();
        }
        if (NullGameSettings.IsMatch(levelJson))
        {
            return defaults.Copy();
        }

        GameSettingsEnvelope envelope = new GameSettingsEnvelope();
        envelope.gameSettings = defaults.Copy();
        try
        {
            JsonUtility.FromJsonOverwrite(levelJson, envelope);
        }
        catch (System.Exception e)
        {
            Debug.LogWarning(LogPrefix + "invalid gameSettings JSON, using inspector values: " + e.Message);
            return defaults.Copy();
        }

        if (envelope.gameSettings == null)
        {
            return defaults.Copy();
        }
        return envelope.gameSettings;
    }
}
