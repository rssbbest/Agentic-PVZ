using System;
using UnityEngine;

// NEW: who owns Time.timeScale and the CRT right now
public enum GamePhase
{
    Running,
    InteractionEngine,
    ReinforcementPause,
    Over
}

// NEW: backend decision flag for a reinforcement ping
[Serializable]
public class ReinforcementDecision
{
    public bool placeDefences;
    public int coins; // NEW: coin balance after this decision
}

// NEW: pure interval clock for the reinforcement ping (game seconds, caller passes Time.time)
public class ReinforcementScheduler
{
    float intervalSeconds;
    bool isEnabled;
    float cycleStart;
    bool fired;    // returned true this cycle, until Restart
    bool stopped;  // game over / wave done, permanent

    public ReinforcementScheduler(float intervalSeconds, bool enabled, float now)
    {
        this.intervalSeconds = intervalSeconds;
        isEnabled = enabled;
        if (intervalSeconds <= 0f)
        {
            isEnabled = false;
        }
        Restart(now);
    }

    // new cycle starts now, clears the latch
    public void Restart(float now)
    {
        cycleStart = now;
        fired = false;
    }

    public bool IsDue(float now)
    {
        return now - cycleStart >= intervalSeconds;
    }

    public bool IsStopped()
    {
        return stopped;
    }

    // true = start a reinforcement pause now
    public bool Tick(float now, GamePhase phase, bool waveCompleted, bool gameOver, bool crtIdle)
    {
        if (!isEnabled || stopped)
        {
            return false;
        }
        if (waveCompleted || gameOver || phase == GamePhase.Over)
        {
            stopped = true;
            return false;
        }
        if (fired)
        {
            return false;
        }
        if (!IsDue(now))
        {
            return false;
        }
        // UPDATED: due during a taunt too (the game cuts the taunt short: the agent is
        // asked on schedule, never later because a taunt was showing). Otherwise
        // deferred until the CRT is fully zoomed back in after the last pause.
        if (phase == GamePhase.InteractionEngine)
        {
            fired = true;
            return true;
        }
        if (phase != GamePhase.Running || !crtIdle)
        {
            return false;
        }
        fired = true;
        return true;
    }
}

// NEW: pure helpers for the reinforcement pause
public static class ReinforcementRules
{
    public const string LogPrefix = "[Reinforcements] ";

    // Over is final
    public static GamePhase NextPhase(GamePhase current, GamePhase requested)
    {
        if (current == GamePhase.Over)
        {
            return GamePhase.Over;
        }
        return requested;
    }

    // place vs skip; blank or unreadable = skip, never throws
    public static bool ShouldPlace(string responseJson)
    {
        if (string.IsNullOrWhiteSpace(responseJson))
        {
            return false;
        }

        ReinforcementDecision decision = null;
        try
        {
            decision = JsonUtility.FromJson<ReinforcementDecision>(responseJson);
        }
        catch (Exception e)
        {
            Debug.LogWarning(LogPrefix + "unreadable response, skipping: " + e.Message);
            return false;
        }

        if (decision == null)
        {
            return false;
        }
        return decision.placeDefences;
    }

    // NEW: coin balance from a reinforcement response; missing or unreadable = currentCoins
    public static int ParseCoins(string responseJson, int currentCoins)
    {
        if (string.IsNullOrWhiteSpace(responseJson))
        {
            return currentCoins;
        }

        ReinforcementDecision decision = new ReinforcementDecision();
        decision.coins = currentCoins;
        try
        {
            JsonUtility.FromJsonOverwrite(responseJson, decision);
        }
        catch (Exception e)
        {
            Debug.LogWarning(LogPrefix + "unreadable coins, keeping " + currentCoins + ": " + e.Message);
            return currentCoins;
        }
        return decision.coins;
    }

    // startScale at the start, exactly 0 at the end, synced to the CRT zoom-out
    public static float EaseDownScale(float elapsed, float duration, AnimationCurve ease, float startScale)
    {
        if (startScale < 0f)
        {
            startScale = 0f;
        }
        if (duration <= 0f || elapsed >= duration)
        {
            return 0f;
        }
        if (elapsed <= 0f)
        {
            return startScale;
        }
        float eased = EvaluateEase(ease, elapsed / duration);
        return startScale * (1f - eased);
    }

    // 0 at the start, exactly 1 at the end, synced to the CRT zoom-in
    public static float EaseUpScale(float elapsed, float duration, AnimationCurve ease)
    {
        if (duration <= 0f || elapsed >= duration)
        {
            return 1f;
        }
        if (elapsed <= 0f)
        {
            return 0f;
        }
        return EvaluateEase(ease, elapsed / duration);
    }

    // same progress the CRT uses, clamped so overshooting curves can't go negative
    static float EvaluateEase(AnimationCurve ease, float progress)
    {
        float p = Mathf.Clamp01(progress);
        if (ease == null)
        {
            return p;
        }
        return Mathf.Clamp01(ease.Evaluate(p));
    }
}
