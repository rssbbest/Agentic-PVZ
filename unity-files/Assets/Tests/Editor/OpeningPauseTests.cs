using System.Reflection;
using NUnit.Framework;
using UnityEngine;

public class OpeningPauseTests
{
    GameObject managerObject;
    GameObject networkObject;
    GameManager manager;
    float oldScale;
    NetworkManager oldNetwork;
    GameManager oldManager;

    static void SetField(object target, string name, object value)
    {
        target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)
            .SetValue(target, value);
    }

    [SetUp]
    public void SetUp()
    {
        oldScale = Time.timeScale;
        oldNetwork = NetworkManager.Instance;
        oldManager = GameManager.Instance;
        managerObject = new GameObject("opening-test-manager");
        managerObject.SetActive(false);
        manager = managerObject.AddComponent<GameManager>();
        networkObject = new GameObject("opening-test-network");
        networkObject.SetActive(false);
        var network = networkObject.AddComponent<NetworkManager>();
        typeof(NetworkManager).GetProperty("Instance").GetSetMethod(true)
            .Invoke(null, new object[] { network });
        SetField(manager, "quickTestMode", false);
        SetField(manager, "phase", GamePhase.Running);
        Time.timeScale = 2f;
    }

    [TearDown]
    public void TearDown()
    {
        Object.DestroyImmediate(managerObject);
        Object.DestroyImmediate(networkObject);
        typeof(NetworkManager).GetProperty("Instance").GetSetMethod(true)
            .Invoke(null, new object[] { oldNetwork });
        GameManager.Instance = oldManager;
        Time.timeScale = oldScale;
    }

    [TestCase(true)]
    [TestCase(false)]
    public void OpeningFreezesSynchronouslyEvenWhenTimedReinforcementsAreDisabled(bool enabled)
    {
        SetField(manager, "reinforcementsEnabled", enabled);
        var routine = manager.OpeningAfterFirstSpawn();
        // Do NOT advance the iterator: freezing must happen before Unity can
        // run any other Update/physics step in the first enemy's spawn frame.
        Assert.That(routine, Is.Not.Null);
        Assert.That(Time.timeScale, Is.Zero);
        Assert.That(manager.OpeningAfterFirstSpawn(), Is.Null, "one opening only");
    }

    [Test]
    public void FinishedGameCannotStartAnOpening()
    {
        SetField(manager, "phase", GamePhase.Over);
        Assert.That(manager.OpeningAfterFirstSpawn(), Is.Null);
        Assert.That(Time.timeScale, Is.EqualTo(2f));
    }

    [Test]
    public void LocalQuickTestDoesNotWaitForAnSdk()
    {
        SetField(manager, "quickTestMode", true);
        Assert.That(manager.OpeningAfterFirstSpawn(), Is.Null);
        Assert.That(Time.timeScale, Is.EqualTo(2f));
    }

    [Test]
    public void DisabledTimerStaysDisabledAfterOpeningRestartsIt()
    {
        var timer = new ReinforcementScheduler(3f, false, 0f);
        timer.Restart(0.3f);
        Assert.That(timer.Tick(100f, GamePhase.Running, false, false, true), Is.False);
    }
}
