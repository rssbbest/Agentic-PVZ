using System;
using System.Collections.Generic;
using System.Text;
using System.Text.RegularExpressions;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

// NEW: EditMode tests for the mid-run defence placement pure logic
// (ReinforcementScheduler, ReinforcementRules, PlacementLedger, response schema)
public class MidRunDefencePlacementTests
{
    const int Iterations = 500;
    const float Tolerance = 1e-5f;

    static readonly GamePhase[] AllPhases =
    {
        GamePhase.Running,
        GamePhase.InteractionEngine,
        GamePhase.ReinforcementPause,
        GamePhase.Over
    };

    static readonly string[] PrefabNames =
    {
        "defenceSoilder",
        "defenceArcher",
        "defencePenguin",
        "defenceShield"
    };

    // test-side mirror of the backend response, built from the start-of-run wire DTOs
    [Serializable]
    public class TestReinforcementResponse
    {
        public bool placeDefences;
        public List<DefencePlacement> defencePlacementData;
    }

    // ------------------------------------------------------------------ helpers

    static int NewSeed()
    {
        return Environment.TickCount;
    }

    static float Range(System.Random rng, float min, float max)
    {
        return min + (float)rng.NextDouble() * (max - min);
    }

    static bool Chance(System.Random rng, double probability)
    {
        return rng.NextDouble() < probability;
    }

    static GamePhase RandomPhase(System.Random rng)
    {
        return AllPhases[rng.Next(AllPhases.Length)];
    }

    static string RandomLetters(System.Random rng, int minLength, int maxLength)
    {
        int length = rng.Next(minLength, maxLength + 1);
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < length; i++)
        {
            char c = (char)('a' + rng.Next(26));
            if (Chance(rng, 0.3))
            {
                c = char.ToUpperInvariant(c);
            }
            sb.Append(c);
        }
        return sb.ToString();
    }

    static string RandomAscii(System.Random rng, int maxLength)
    {
        int length = rng.Next(0, maxLength + 1);
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < length; i++)
        {
            sb.Append((char)rng.Next(32, 127));
        }
        return sb.ToString();
    }

    // EaseInOut, Linear, null, or a random 3-5 key curve with values in [-0.5, 1.5]
    static AnimationCurve RandomCurve(System.Random rng, out bool monotone)
    {
        int pick = rng.Next(4);
        if (pick == 0)
        {
            monotone = true;
            return AnimationCurve.EaseInOut(0f, 0f, 1f, 1f);
        }
        if (pick == 1)
        {
            monotone = true;
            return AnimationCurve.Linear(0f, 0f, 1f, 1f);
        }
        if (pick == 2)
        {
            monotone = true; // null = linear fallback
            return null;
        }

        monotone = false;
        int keyCount = rng.Next(3, 6);
        Keyframe[] keys = new Keyframe[keyCount];
        for (int i = 0; i < keyCount; i++)
        {
            float time = (float)i / (keyCount - 1);
            keys[i] = new Keyframe(time, Range(rng, -0.5f, 1.5f));
        }
        return new AnimationCurve(keys);
    }

    static List<DefencePlacement> RandomCards(System.Random rng, int maxCount)
    {
        int count = rng.Next(0, maxCount + 1);
        List<DefencePlacement> cards = new List<DefencePlacement>();
        for (int i = 0; i < count; i++)
        {
            DefencePlacement card = new DefencePlacement();
            card.defenceId = rng.Next(0, 8);
            card.defenceGridPosition = new GridPosition();
            card.defenceGridPosition.x = rng.Next(0, 11);
            card.defenceGridPosition.y = rng.Next(0, 6);
            cards.Add(card);
        }
        return cards;
    }

    // ------------------------------------------------------------------ ReinforcementScheduler examples

    [Test]
    public void Scheduler_ExampleSequence()
    {
        ReinforcementScheduler scheduler = new ReinforcementScheduler(30f, true, 0f);
        Assert.IsFalse(scheduler.Tick(29.9f, GamePhase.Running, false, false, true), "not due");
        Assert.IsFalse(scheduler.Tick(31f, GamePhase.Running, false, false, false), "CRT still zooming in");
        Assert.IsTrue(scheduler.Tick(31.5f, GamePhase.Running, false, false, true), "fires once free");
        Assert.IsFalse(scheduler.Tick(31.6f, GamePhase.Running, false, false, true), "already fired this cycle");
        scheduler.Restart(70f);
        Assert.IsFalse(scheduler.Tick(80f, GamePhase.Running, false, true, true), "game over");
        Assert.IsTrue(scheduler.IsStopped());
        Assert.IsFalse(scheduler.Tick(200f, GamePhase.Running, false, false, true), "stopped for good");
    }

    [Test]
    public void Scheduler_ATauntNeverDelaysAPause()
    {
        // Game time (Time.time) runs slower during the taunt's slow motion, so pauses
        // come later in real time, exactly in step with the game; but a due pause
        // starts at once instead of waiting for the taunt to end.
        ReinforcementScheduler scheduler = new ReinforcementScheduler(10f, true, 0f);
        Assert.IsFalse(scheduler.Tick(9.5f, GamePhase.InteractionEngine, false, false, false), "not due yet");
        Assert.IsTrue(scheduler.Tick(10f, GamePhase.InteractionEngine, false, false, false), "due: cuts the taunt");
        Assert.IsFalse(scheduler.Tick(10.1f, GamePhase.InteractionEngine, false, false, false), "once per cycle");
        Assert.IsFalse(scheduler.Tick(30f, GamePhase.ReinforcementPause, false, false, false), "never during a pause");
    }

    [Test]
    public void Scheduler_FirstPingExactlyOneIntervalAfterStart()
    {
        ReinforcementScheduler scheduler = new ReinforcementScheduler(10f, true, 0f);
        Assert.IsFalse(scheduler.Tick(9.99f, GamePhase.Running, false, false, true));
        Assert.IsTrue(scheduler.Tick(10f, GamePhase.Running, false, false, true));
    }

    [Test]
    public void Scheduler_RestartClearsLatch()
    {
        ReinforcementScheduler scheduler = new ReinforcementScheduler(5f, true, 0f);
        Assert.IsTrue(scheduler.Tick(5f, GamePhase.Running, false, false, true));
        scheduler.Restart(6f);
        Assert.IsFalse(scheduler.Tick(10.9f, GamePhase.Running, false, false, true));
        Assert.IsTrue(scheduler.Tick(11f, GamePhase.Running, false, false, true));
    }

    [Test]
    public void Scheduler_DisabledOrBadIntervalNeverFires()
    {
        ReinforcementScheduler off = new ReinforcementScheduler(5f, false, 0f);
        ReinforcementScheduler zero = new ReinforcementScheduler(0f, true, 0f);
        ReinforcementScheduler negative = new ReinforcementScheduler(-3f, true, 0f);
        Assert.IsFalse(off.Tick(100f, GamePhase.Running, false, false, true));
        Assert.IsFalse(zero.Tick(100f, GamePhase.Running, false, false, true));
        Assert.IsFalse(negative.Tick(100f, GamePhase.Running, false, false, true));
    }

    [Test]
    public void Scheduler_StopsOnWaveCompletedGameOverOrPhaseOver()
    {
        ReinforcementScheduler wave = new ReinforcementScheduler(5f, true, 0f);
        Assert.IsFalse(wave.Tick(1f, GamePhase.Running, true, false, true));
        wave.Restart(2f);
        Assert.IsFalse(wave.Tick(100f, GamePhase.Running, false, false, true));

        ReinforcementScheduler over = new ReinforcementScheduler(5f, true, 0f);
        Assert.IsFalse(over.Tick(1f, GamePhase.Running, false, true, true));
        Assert.IsFalse(over.Tick(100f, GamePhase.Running, false, false, true));

        ReinforcementScheduler phaseOver = new ReinforcementScheduler(5f, true, 0f);
        Assert.IsFalse(phaseOver.Tick(1f, GamePhase.Over, false, false, true));
        Assert.IsFalse(phaseOver.Tick(100f, GamePhase.Running, false, false, true));
    }

    // ------------------------------------------------------------------ ReinforcementScheduler properties

    // Property 1: No ping before the interval (Req 2.2, 2.4)
    [Test]
    public void Property1_NoPingBeforeInterval()
    {
        int seed = NewSeed();
        System.Random rng = new System.Random(seed);
        for (int i = 0; i < Iterations; i++)
        {
            float interval = Range(rng, 0.1f, 120f);
            float t0 = Range(rng, 0f, 1000f);
            float now = t0 + interval * Range(rng, 0f, 0.99f);
            ReinforcementScheduler scheduler = new ReinforcementScheduler(interval, true, t0);
            bool result = scheduler.Tick(now, RandomPhase(rng), Chance(rng, 0.5), Chance(rng, 0.5), Chance(rng, 0.5));
            Assert.IsFalse(result, "seed=" + seed + " iteration=" + i);
        }
    }

    // Property 2: A due ping fires as soon as the game is free (Req 2.5)
    [Test]
    public void Property2_DuePingFiresWhenFree()
    {
        int seed = NewSeed();
        System.Random rng = new System.Random(seed);
        for (int i = 0; i < Iterations; i++)
        {
            float interval = Range(rng, 0.1f, 120f);
            float t0 = Range(rng, 0f, 1000f);
            float now = t0 + interval + Range(rng, 0.01f, 50f);
            ReinforcementScheduler scheduler = new ReinforcementScheduler(interval, true, t0);
            Assert.IsTrue(scheduler.Tick(now, GamePhase.Running, false, false, true), "seed=" + seed + " iteration=" + i);
        }
    }

    // Property 3: Deferral, not loss (Req 2.3, 2.5, 2.6)
    [Test]
    public void Property3_DeferralNotLoss()
    {
        int seed = NewSeed();
        System.Random rng = new System.Random(seed);
        for (int i = 0; i < Iterations; i++)
        {
            float interval = Range(rng, 0.1f, 60f);
            float now = interval + Range(rng, 0.01f, 5f);
            ReinforcementScheduler scheduler = new ReinforcementScheduler(interval, true, 0f);

            int blocked = rng.Next(0, 21);
            for (int k = 0; k < blocked; k++)
            {
                GamePhase phase = GamePhase.Running;
                bool crtIdle = true;
                // UPDATED: a taunt no longer defers a pause (see Scheduler_ATauntNeverDelaysAPause)
                int reason = rng.Next(2);
                if (reason == 0)
                {
                    phase = GamePhase.ReinforcementPause;
                    crtIdle = Chance(rng, 0.5);
                }
                else
                {
                    crtIdle = false;
                }
                Assert.IsFalse(scheduler.Tick(now, phase, false, false, crtIdle), "seed=" + seed + " iteration=" + i + " blocked tick=" + k);
                now += Range(rng, 0f, 5f);
            }
            Assert.IsTrue(scheduler.Tick(now, GamePhase.Running, false, false, true), "seed=" + seed + " iteration=" + i);
        }
    }

    // Property 4: At most one ping per cycle (Req 2.7, 3.3)
    // Property 5: Stopping is permanent (Req 2.8)
    [Test]
    public void Property4And5_OnePingPerCycleAndPermanentStop()
    {
        int seed = NewSeed();
        System.Random rng = new System.Random(seed);
        for (int i = 0; i < Iterations; i++)
        {
            float interval = Range(rng, 0.1f, 20f);
            float now = Range(rng, 0f, 100f);
            ReinforcementScheduler scheduler = new ReinforcementScheduler(interval, true, now);
            int truesThisCycle = 0;
            bool stopSeen = false;

            int steps = rng.Next(0, 201);
            for (int s = 0; s < steps; s++)
            {
                now += Range(rng, 0f, 5f);
                if (Chance(rng, 0.05))
                {
                    scheduler.Restart(now);
                    truesThisCycle = 0;
                }

                GamePhase phase = RandomPhase(rng);
                bool waveCompleted = Chance(rng, 0.02);
                bool gameOver = Chance(rng, 0.02);
                bool crtIdle = Chance(rng, 0.7);
                bool stopsNow = waveCompleted || gameOver || phase == GamePhase.Over;

                bool result = scheduler.Tick(now, phase, waveCompleted, gameOver, crtIdle);
                if (stopsNow)
                {
                    stopSeen = true;
                }
                if (result)
                {
                    truesThisCycle++;
                    // UPDATED: a due pause also cuts a taunt short (the taunt keeps the CRT on)
                    Assert.IsTrue(phase == GamePhase.Running || phase == GamePhase.InteractionEngine,
                                  "seed=" + seed + " fired outside Running or a taunt");
                    Assert.IsTrue(crtIdle || phase == GamePhase.InteractionEngine, "seed=" + seed + " fired while CRT busy");
                }
                Assert.LessOrEqual(truesThisCycle, 1, "seed=" + seed + " iteration=" + i + " step=" + s);
                if (stopSeen)
                {
                    Assert.IsFalse(result, "seed=" + seed + " fired after stop, iteration=" + i + " step=" + s);
                    Assert.IsTrue(scheduler.IsStopped(), "seed=" + seed);
                }
            }
        }
    }

    // Property 6: Disabled means never (Req 1.4, 1.5)
    [Test]
    public void Property6_DisabledMeansNever()
    {
        int seed = NewSeed();
        System.Random rng = new System.Random(seed);
        for (int i = 0; i < Iterations; i++)
        {
            bool enabled = Chance(rng, 0.5);
            float interval = Range(rng, -5f, 0f);
            if (!enabled)
            {
                interval = Range(rng, -5f, 120f);
            }
            float now = Range(rng, 0f, 100f);
            ReinforcementScheduler scheduler = new ReinforcementScheduler(interval, enabled, now);

            int steps = rng.Next(0, 101);
            for (int s = 0; s < steps; s++)
            {
                now += Range(rng, 0f, 50f);
                if (Chance(rng, 0.1))
                {
                    scheduler.Restart(now);
                }
                bool result = scheduler.Tick(now, RandomPhase(rng), Chance(rng, 0.1), Chance(rng, 0.1), Chance(rng, 0.7));
                Assert.IsFalse(result, "seed=" + seed + " iteration=" + i + " step=" + s);
            }
        }
    }

    // ------------------------------------------------------------------ NextPhase

    // Property 7: Over is absorbing (Req 4.6, 4.7), exhaustive over all 16 pairs
    [Test]
    public void Property7_OverIsAbsorbing()
    {
        foreach (GamePhase current in AllPhases)
        {
            foreach (GamePhase requested in AllPhases)
            {
                GamePhase result = ReinforcementRules.NextPhase(current, requested);
                if (current == GamePhase.Over)
                {
                    Assert.AreEqual(GamePhase.Over, result, current + " -> " + requested);
                }
                else
                {
                    Assert.AreEqual(requested, result, current + " -> " + requested);
                }
            }
        }
    }

    // ------------------------------------------------------------------ ShouldPlace examples

    [Test]
    public void ShouldPlace_Examples()
    {
        Assert.IsTrue(ReinforcementRules.ShouldPlace("{\"placeDefences\": true, \"defencePlacementData\": [ {\"defenceId\": 0, \"defenceGridPosition\": {\"x\": 3, \"y\": 2}} ]}"));
        Assert.IsTrue(ReinforcementRules.ShouldPlace("{\"placeDefences\": true, \"extra\": 5}"));
        Assert.IsFalse(ReinforcementRules.ShouldPlace("{\"placeDefences\": false}"));
        Assert.IsFalse(ReinforcementRules.ShouldPlace("{}"));
        Assert.IsFalse(ReinforcementRules.ShouldPlace("{\"defencePlacementData\": []}"));
        Assert.IsFalse(ReinforcementRules.ShouldPlace(""));
        Assert.IsFalse(ReinforcementRules.ShouldPlace(null));
        Assert.IsFalse(ReinforcementRules.ShouldPlace("   "));
    }

    [Test]
    public void ShouldPlace_NonJsonWarnsAndSkips()
    {
        LogAssert.Expect(LogType.Warning, new Regex("^\\[Reinforcements\\] unreadable response"));
        Assert.IsFalse(ReinforcementRules.ShouldPlace("<html>502</html>"));
    }

    // ------------------------------------------------------------------ ShouldPlace properties

    // Property 11: Decision parsing is total (Req 8.3, 8.4, 8.5, 8.6, 8.7)
    [Test]
    public void Property11_DecisionParsingIsTotal()
    {
        int seed = NewSeed();
        System.Random rng = new System.Random(seed);
        for (int i = 0; i < 200; i++)
        {
            bool placeDefences = Chance(rng, 0.5);
            TestReinforcementResponse response = new TestReinforcementResponse();
            response.placeDefences = placeDefences;
            response.defencePlacementData = RandomCards(rng, 5);
            string valid = JsonUtility.ToJson(response);

            // valid responses: decision equals the flag
            Assert.AreEqual(placeDefences, ReinforcementRules.ShouldPlace(valid), "seed=" + seed + " json=" + valid);

            // random truncations and random ASCII: never throws
            string truncated = valid.Substring(0, rng.Next(0, valid.Length + 1));
            string noise = RandomAscii(rng, 40);
            try
            {
                ReinforcementRules.ShouldPlace(truncated);
                ReinforcementRules.ShouldPlace(noise);
            }
            catch (Exception e)
            {
                Assert.Fail("seed=" + seed + " ShouldPlace threw: " + e.Message);
            }
        }
    }

    // ------------------------------------------------------------------ Schema compatibility

    [Test]
    public void Schema_DesignPlaceExampleParsesAsStartOfRunCards()
    {
        string json = "{\"placeDefences\": true, \"defencePlacementData\": [ { \"defenceId\": 0, \"defenceGridPosition\": { \"x\": 3, \"y\": 2 } } ]}";
        DefencePlacementDataWrapper batch = JsonUtility.FromJson<DefencePlacementDataWrapper>(json);
        Assert.IsNotNull(batch);
        Assert.IsNotNull(batch.defencePlacementData);
        Assert.AreEqual(1, batch.defencePlacementData.Count);
        Assert.AreEqual(0, batch.defencePlacementData[0].defenceId);
        Assert.AreEqual(new Vector2Int(3, 2), batch.defencePlacementData[0].defenceGridPosition);
    }

    // Property 12: Mid-run responses use the start-of-run schema (Req 8.3, 9.1, 16.2)
    [Test]
    public void Property12_ResponseUsesStartOfRunSchema()
    {
        int seed = NewSeed();
        System.Random rng = new System.Random(seed);
        for (int i = 0; i < Iterations; i++)
        {
            TestReinforcementResponse response = new TestReinforcementResponse();
            response.placeDefences = true;
            response.defencePlacementData = RandomCards(rng, 10);
            string json = JsonUtility.ToJson(response);

            Assert.IsTrue(ReinforcementRules.ShouldPlace(json), "seed=" + seed + " json=" + json);

            DefencePlacementDataWrapper batch = JsonUtility.FromJson<DefencePlacementDataWrapper>(json);
            Assert.IsNotNull(batch, "seed=" + seed);
            Assert.IsNotNull(batch.defencePlacementData, "seed=" + seed);
            Assert.AreEqual(response.defencePlacementData.Count, batch.defencePlacementData.Count, "seed=" + seed + " json=" + json);
            for (int c = 0; c < response.defencePlacementData.Count; c++)
            {
                DefencePlacement sent = response.defencePlacementData[c];
                DefencePlacementCard read = batch.defencePlacementData[c];
                Assert.AreEqual(sent.defenceId, read.defenceId, "seed=" + seed + " card=" + c);
                Assert.AreEqual(sent.defenceGridPosition.x, read.defenceGridPosition.x, "seed=" + seed + " card=" + c);
                Assert.AreEqual(sent.defenceGridPosition.y, read.defenceGridPosition.y, "seed=" + seed + " card=" + c);
            }
        }
    }

    // ------------------------------------------------------------------ Easing examples

    [Test]
    public void Easing_Examples()
    {
        AnimationCurve ease = AnimationCurve.EaseInOut(0f, 0f, 1f, 1f);

        Assert.AreEqual(1.5f, ReinforcementRules.EaseDownScale(0f, 1.2f, ease, 1.5f));
        Assert.AreEqual(0.5f, ReinforcementRules.EaseDownScale(0.6f, 1.2f, ease, 1f), 1e-4f);
        Assert.AreEqual(0f, ReinforcementRules.EaseDownScale(1.2f, 1.2f, ease, 1f));
        Assert.AreEqual(0f, ReinforcementRules.EaseDownScale(5f, 1.2f, ease, 1f));
        Assert.AreEqual(0f, ReinforcementRules.EaseDownScale(0.5f, 0f, ease, 1f));
        Assert.AreEqual(0f, ReinforcementRules.EaseDownScale(0.3f, 1.2f, ease, -1f));
        Assert.AreEqual(0.75f, ReinforcementRules.EaseDownScale(0.3f, 1.2f, null, 1f), Tolerance);

        Assert.AreEqual(0f, ReinforcementRules.EaseUpScale(0f, 1.2f, ease));
        Assert.AreEqual(1f, ReinforcementRules.EaseUpScale(1.2f, 1.2f, ease));
        Assert.AreEqual(1f, ReinforcementRules.EaseUpScale(1.3f, 1.2f, ease));
        Assert.AreEqual(1f, ReinforcementRules.EaseUpScale(0.5f, 0f, ease));
        Assert.AreEqual(0.25f, ReinforcementRules.EaseUpScale(0.3f, 1.2f, null), Tolerance);
    }

    [Test]
    public void Easing_OvershootingCurveStaysInRange()
    {
        AnimationCurve overshoot = new AnimationCurve(new Keyframe(0f, -0.4f), new Keyframe(0.5f, 1.4f), new Keyframe(1f, 1.3f));
        for (int i = 1; i < 100; i++)
        {
            float elapsed = i * 0.012f;
            float down = ReinforcementRules.EaseDownScale(elapsed, 1.2f, overshoot, 1f);
            float up = ReinforcementRules.EaseUpScale(elapsed, 1.2f, overshoot);
            Assert.That(down, Is.InRange(0f, 1f), "down at " + elapsed);
            Assert.That(up, Is.InRange(0f, 1f), "up at " + elapsed);
        }
    }

    // ------------------------------------------------------------------ Easing properties

    // Property 8: Ease-down bounds, end values and monotonicity (Req 5.7, 5.8, 5.9, 5.12)
    [Test]
    public void Property8_EaseDownBoundsEndsAndMonotonicity()
    {
        int seed = NewSeed();
        System.Random rng = new System.Random(seed);
        for (int i = 0; i < Iterations; i++)
        {
            bool monotone;
            AnimationCurve ease = RandomCurve(rng, out monotone);
            float elapsed = Range(rng, -1f, 3f);
            float duration = Range(rng, -0.5f, 3f);
            float startScale = Range(rng, -0.5f, 2f);
            float effectiveStart = Mathf.Max(0f, startScale);
            string context = "seed=" + seed + " iteration=" + i + " elapsed=" + elapsed + " duration=" + duration + " start=" + startScale;

            float result = ReinforcementRules.EaseDownScale(elapsed, duration, ease, startScale);
            Assert.That(result, Is.InRange(0f, effectiveStart), context);

            if (duration <= 0f || elapsed >= duration)
            {
                Assert.AreEqual(0f, result, context);
            }
            else if (elapsed <= 0f)
            {
                Assert.AreEqual(effectiveStart, result, context);
            }

            if (monotone)
            {
                float a = Range(rng, -1f, 3f);
                float b = Range(rng, -1f, 3f);
                float e1 = Mathf.Min(a, b);
                float e2 = Mathf.Max(a, b);
                float r1 = ReinforcementRules.EaseDownScale(e1, duration, ease, startScale);
                float r2 = ReinforcementRules.EaseDownScale(e2, duration, ease, startScale);
                Assert.GreaterOrEqual(r1 + Tolerance, r2, context + " e1=" + e1 + " e2=" + e2);
            }
        }
    }

    // Property 9: Ease-up mirrors ease-down (Req 12.8, 12.9, 12.10, 12.11, 12.12)
    [Test]
    public void Property9_EaseUpMirrorsEaseDown()
    {
        int seed = NewSeed();
        System.Random rng = new System.Random(seed);
        for (int i = 0; i < Iterations; i++)
        {
            bool monotone;
            AnimationCurve ease = RandomCurve(rng, out monotone);
            float elapsed = Range(rng, -1f, 3f);
            float duration = Range(rng, -0.5f, 3f);
            string context = "seed=" + seed + " iteration=" + i + " elapsed=" + elapsed + " duration=" + duration;

            float up = ReinforcementRules.EaseUpScale(elapsed, duration, ease);
            Assert.That(up, Is.InRange(0f, 1f), context);

            // duration <= 0 returns 1 (Req 12.9); exactly 0 at elapsed <= 0 only with a positive duration (Req 12.10)
            if (duration <= 0f || elapsed >= duration)
            {
                Assert.AreEqual(1f, up, context);
            }
            else if (elapsed <= 0f)
            {
                Assert.AreEqual(0f, up, context);
            }
            else
            {
                float down = ReinforcementRules.EaseDownScale(elapsed, duration, ease, 1f);
                Assert.AreEqual(1f, up + down, Tolerance, context);
            }

            if (monotone)
            {
                float a = Range(rng, -1f, 3f);
                float b = Range(rng, -1f, 3f);
                float e1 = Mathf.Min(a, b);
                float e2 = Mathf.Max(a, b);
                float r1 = ReinforcementRules.EaseUpScale(e1, duration, ease);
                float r2 = ReinforcementRules.EaseUpScale(e2, duration, ease);
                Assert.LessOrEqual(r1, r2 + Tolerance, context + " e1=" + e1 + " e2=" + e2);
            }
        }
    }

    // Property 10: Time scale follows the CRT frame by frame (Req 5.4, 5.5, 12.5, 12.6)
    // mirrors CRTPauseController.Animate: time += dt; amount = LerpUnclamped(from, target, ease(Clamp01(time / dur)))
    [Test]
    public void Property10_TimeScaleFollowsCrtFrameByFrame()
    {
        int seed = NewSeed();
        System.Random rng = new System.Random(seed);
        for (int i = 0; i < Iterations; i++)
        {
            AnimationCurve ease = AnimationCurve.EaseInOut(0f, 0f, 1f, 1f);
            if (Chance(rng, 0.5))
            {
                ease = AnimationCurve.Linear(0f, 0f, 1f, 1f);
            }
            float duration = Mathf.Max(0.01f, Range(rng, 0.01f, 3f));
            float startScale = Range(rng, 0f, 2f);
            string context = "seed=" + seed + " iteration=" + i + " duration=" + duration + " start=" + startScale;

            // zoom-out (CRT 0 -> 1) next to the ease-down loop
            float crtTime = 0f;
            float elapsed = 0f;
            float amount = 0f;
            float timeScale = startScale;
            int crtFrames = 0;
            int easeFrames = 0;
            while (crtTime < duration || elapsed < duration)
            {
                float dt = Range(rng, 0.001f, 0.1f);
                if (crtTime < duration)
                {
                    crtTime += dt;
                    amount = Mathf.LerpUnclamped(0f, 1f, ease.Evaluate(Mathf.Clamp01(crtTime / duration)));
                    crtFrames++;
                }
                if (elapsed < duration)
                {
                    elapsed += dt;
                    timeScale = ReinforcementRules.EaseDownScale(elapsed, duration, ease, startScale);
                    easeFrames++;
                }
                Assert.AreEqual(crtFrames, easeFrames, context);
                Assert.AreEqual(startScale * (1f - amount), timeScale, 1e-4f, context + " frame=" + crtFrames);
            }
            Assert.AreEqual(0f, timeScale, context);
            Assert.AreEqual(1f, amount, 1e-4f, context);

            // zoom-in (CRT 1 -> 0) next to the ease-up loop
            crtTime = 0f;
            elapsed = 0f;
            crtFrames = 0;
            easeFrames = 0;
            while (crtTime < duration || elapsed < duration)
            {
                float dt = Range(rng, 0.001f, 0.1f);
                if (crtTime < duration)
                {
                    crtTime += dt;
                    amount = Mathf.LerpUnclamped(1f, 0f, ease.Evaluate(Mathf.Clamp01(crtTime / duration)));
                    crtFrames++;
                }
                if (elapsed < duration)
                {
                    elapsed += dt;
                    timeScale = ReinforcementRules.EaseUpScale(elapsed, duration, ease);
                    easeFrames++;
                }
                Assert.AreEqual(crtFrames, easeFrames, context);
                Assert.AreEqual(1f - amount, timeScale, 1e-4f, context + " frame=" + crtFrames);
            }
            Assert.AreEqual(1f, timeScale, context);
            Assert.AreEqual(0f, amount, 1e-4f, context);
        }
    }

    // ------------------------------------------------------------------ PlacementLedger examples

    [Test]
    public void Ledger_RunWideNamingTable()
    {
        PlacementLedger ledger = new PlacementLedger();

        // start-of-run
        Assert.AreEqual("defenceSoilder_0", ledger.RegisterPlacement("defenceSoilder"));
        Assert.AreEqual("defenceSoilder_1", ledger.RegisterPlacement("defenceSoilder"));
        Assert.AreEqual("defenceArcher_0", ledger.RegisterPlacement("defenceArcher"));
        Assert.AreEqual(3, ledger.PlacedCount());

        // mid-run 1
        Assert.AreEqual("defenceSoilder_2", ledger.RegisterPlacement("defenceSoilder"));
        Assert.AreEqual("defencePenguin_0", ledger.RegisterPlacement("defencePenguin"));
        Assert.AreEqual(5, ledger.PlacedCount());

        // mid-run 2
        Assert.AreEqual("defenceArcher_1", ledger.RegisterPlacement("defenceArcher"));
        Assert.AreEqual(6, ledger.PlacedCount());
    }

    // ------------------------------------------------------------------ PlacementLedger properties

    static string RandomPrefabName(System.Random rng)
    {
        if (Chance(rng, 0.7))
        {
            return PrefabNames[rng.Next(PrefabNames.Length)];
        }
        // letters only: non-empty, no "_<digits>" or "(Clone)" suffix (Req 10.4)
        return RandomLetters(rng, 1, 10);
    }

    // Property 13: Names are unique for the whole run (Req 10.1, 10.2, 10.4)
    // Property 14: Defence count accounting (Req 11.1, 11.2)
    [Test]
    public void Property13And14_UniqueNamesAndCountAcrossBatches()
    {
        int seed = NewSeed();
        System.Random rng = new System.Random(seed);
        for (int i = 0; i < Iterations; i++)
        {
            PlacementLedger ledger = new PlacementLedger();
            HashSet<string> seen = new HashSet<string>();
            Dictionary<string, int> earlier = new Dictionary<string, int>();
            int expectedCount = 0;

            int batches = rng.Next(1, 7); // start-of-run + up to 5 mid-run batches
            for (int b = 0; b < batches; b++)
            {
                int batchSize = rng.Next(0, 16);
                for (int c = 0; c < batchSize; c++)
                {
                    string prefab = RandomPrefabName(rng);
                    int k = 0;
                    if (earlier.ContainsKey(prefab))
                    {
                        k = earlier[prefab];
                    }
                    int before = ledger.PlacedCount();

                    string name = ledger.RegisterPlacement(prefab);
                    string context = "seed=" + seed + " iteration=" + i + " batch=" + b + " name=" + name;

                    Assert.AreEqual(prefab + "_" + k, name, context);
                    Assert.IsTrue(seen.Add(name), "duplicate name, " + context);
                    Assert.AreEqual(prefab, LoggingSystem.DeriveType(name), context);
                    Assert.AreEqual(before + 1, ledger.PlacedCount(), context);

                    earlier[prefab] = k + 1;
                    expectedCount++;
                }
                Assert.AreEqual(expectedCount, ledger.PlacedCount(), "seed=" + seed + " iteration=" + i + " batch=" + b);
            }
        }
    }

    // Property 15: Start-of-run naming is preserved (Req 10.5, 11.4)
    // reference = the old InitializeDefences algorithm (local per-prefab counter from 0, maxDefenceCount++)
    [Test]
    public void Property15_StartOfRunNamingPreserved()
    {
        int seed = NewSeed();
        System.Random rng = new System.Random(seed);
        for (int i = 0; i < Iterations; i++)
        {
            PlacementLedger ledger = new PlacementLedger();
            Dictionary<string, int> typeCounters = new Dictionary<string, int>();
            int maxDefenceCount = 0;

            int batchSize = rng.Next(0, 16);
            for (int c = 0; c < batchSize; c++)
            {
                string prefabName = RandomPrefabName(rng);

                int n = 0;
                if (typeCounters.ContainsKey(prefabName))
                {
                    n = typeCounters[prefabName];
                }
                string oldName = prefabName + "_" + n;
                typeCounters[prefabName] = n + 1;
                maxDefenceCount++;

                Assert.AreEqual(oldName, ledger.RegisterPlacement(prefabName), "seed=" + seed + " iteration=" + i + " card=" + c);
            }
            Assert.AreEqual(maxDefenceCount, ledger.PlacedCount(), "seed=" + seed + " iteration=" + i);
        }
    }
}
