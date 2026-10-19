using System.Collections.Generic;
using NUnit.Framework;
using UnityEngine;

public class SeededSpawnTests
{
    static List<int> Rows(int seed, List<int> allowed)
    {
        System.Random rng = SpawnRules.SeededRandom(seed);
        List<int> rows = new List<int>();
        for (int i = 0; i < 20; i++)
        {
            rows.Add(SpawnRules.PickRow(allowed, 5, rng));
        }
        return rows;
    }

    [Test]
    public void SameSeedGivesTheSameRowsEveryGame()
    {
        List<int> allowed = new List<int> { 1, 2, 4 };
        Assert.AreEqual(Rows(1234, allowed), Rows(1234, allowed));
        Assert.AreNotEqual(Rows(1234, allowed), Rows(99, allowed));
        foreach (int row in Rows(1234, allowed))
        {
            Assert.Contains(row, allowed);
        }
        foreach (int row in Rows(7, null)) // null: any row 0..5
        {
            Assert.That(row, Is.InRange(0, 5));
        }
    }

    [Test]
    public void ALevelWithoutASeedSpawnsRandomly()
    {
        Assert.IsNull(SpawnRules.SeededRandom(0));
        List<int> allowed = new List<int> { 1, 2, 4 };
        for (int i = 0; i < 50; i++)
        {
            Assert.Contains(SpawnRules.PickRow(allowed, 5, null), allowed);
        }
    }

    [Test]
    public void TheSimulationStepIsFixed()
    {
        Assert.AreEqual(1f / 60f, SimClock.Step);
        Assert.AreEqual(30, SimClock.Frames(0.5f));
        Assert.AreEqual(1, SimClock.Frames(0f));
    }

    [Test]
    public void TheSeedIsReadFromTheLevel()
    {
        SpawnSettings parsed = SpawnRules.ParseSpawnSettings(
            "{\"spawnSettings\":{\"interArrivalTime\":4.0,\"seed\":5150}}", new SpawnSettings());
        Assert.AreEqual(5150, parsed.seed);
        Assert.AreEqual(4.0f, parsed.interArrivalTime);
        Assert.AreEqual(0, SpawnRules.ParseSpawnSettings("{}", new SpawnSettings()).seed);
    }

    [Test]
    public void TheSnapshotListsAreAlwaysInTheSameOrder()
    {
        LoggingSystem.DefenceStateEntry D(string id, int x, int y)
        {
            return new LoggingSystem.DefenceStateEntry { id = id, position = new Vector2Int(x, y) };
        }
        List<LoggingSystem.DefenceStateEntry> one = new List<LoggingSystem.DefenceStateEntry> { D("b", 2, 0), D("a", 1, 3), D("c", 1, 1) };
        List<LoggingSystem.DefenceStateEntry> two = new List<LoggingSystem.DefenceStateEntry> { D("c", 1, 1), D("b", 2, 0), D("a", 1, 3) };
        one.Sort(LoggingSystem.SnapshotOrder.Defences);
        two.Sort(LoggingSystem.SnapshotOrder.Defences);
        Assert.AreEqual(new[] { "c", "a", "b" }, one.ConvertAll(d => d.id).ToArray());
        Assert.AreEqual(one.ConvertAll(d => d.id), two.ConvertAll(d => d.id));
    }

    [Test]
    public void ADefenceDestroyedBeforeItHitAnythingIsReportedAtItsCell()
    {
        LoggingSystem.Reset();
        LoggingSystem.LogDefenceDestroyed("defenceShield_0", new Vector2Int(6, 1));
        string report = LoggingSystem.Compile(false, "0015", false, "s1");
        StringAssert.Contains("\"position\":{\"x\":6,\"y\":1}", report.Replace(" ", "").Replace("\n", ""));
        LoggingSystem.Reset();
    }
}
