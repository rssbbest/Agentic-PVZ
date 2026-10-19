using System;
using System.Collections.Generic;
using System.Text;
using NUnit.Framework;
using UnityEngine;

// NEW: EditMode tests for mid-run defence detection
// (lane sight skips passed enemies, enemies retarget to the first defence on their ray)
public class MidRunDefenceDetectionTests
{
    const int Iterations = 500;
    const int ColumnCount = 11;

    List<GameObject> created = new List<GameObject>();
    List<GridCell>   row     = new List<GridCell>();

    // ------------------------------------------------------------------ setup

    [SetUp]
    public void SetUp()
    {
        created.Clear();
        row.Clear();
        for (int x = 0; x < ColumnCount; x++)
        {
            GameObject cellObject = new GameObject("cell_" + x);
            created.Add(cellObject);
            GridCell cell = cellObject.AddComponent<GridCell>();
            cell.nodeNumbering = new Vector2Int(x, 0);
            cell.isSomeoneOnThisCell = false;
            row.Add(cell);
        }
    }

    [TearDown]
    public void TearDown()
    {
        foreach (GameObject obj in created)
        {
            if (obj != null)
            {
                UnityEngine.Object.DestroyImmediate(obj);
            }
        }
        created.Clear();
        row.Clear();
    }

    // ------------------------------------------------------------------ helpers

    static int NewSeed()
    {
        return Environment.TickCount;
    }

    static bool Chance(System.Random rng, double probability)
    {
        return rng.NextDouble() < probability;
    }

    void ClearRow()
    {
        foreach (GridCell cell in row)
        {
            cell.isSomeoneOnThisCell = false;
        }
    }

    void SetOccupied(int[] columns)
    {
        ClearRow();
        foreach (int column in columns)
        {
            row[column].isSomeoneOnThisCell = true;
        }
    }

    // each cell occupied with the given probability
    void RandomOccupancy(System.Random rng, double probability)
    {
        foreach (GridCell cell in row)
        {
            cell.isSomeoneOnThisCell = Chance(rng, probability);
        }
    }

    // test-side copy of the old isSomeoneInSight(int) loop: any occupied cell
    static bool OriginalRowScan(List<GridCell> cells)
    {
        foreach (GridCell cell in cells)
        {
            if (cell.isSomeoneOnThisCell)
            {
                return true;
            }
        }
        return false;
    }

    // expected fixed result: some occupied column at or right of minColumn
    static bool ExpectedInSight(List<GridCell> cells, int minColumn)
    {
        foreach (GridCell cell in cells)
        {
            if (cell.isSomeoneOnThisCell && cell.nodeNumbering.x >= minColumn)
            {
                return true;
            }
        }
        return false;
    }

    // C_A: row has enemies, and every one is left of minColumn
    static bool IsBugConditionLaneSight(List<GridCell> cells, int minColumn)
    {
        bool anyOccupied = false;
        foreach (GridCell cell in cells)
        {
            if (!cell.isSomeoneOnThisCell)
            {
                continue;
            }
            anyOccupied = true;
            if (cell.nodeNumbering.x >= minColumn)
            {
                return false;
            }
        }
        return anyOccupied;
    }

    static string Describe(List<GridCell> cells)
    {
        StringBuilder sb = new StringBuilder("{");
        bool first = true;
        foreach (GridCell cell in cells)
        {
            if (!cell.isSomeoneOnThisCell)
            {
                continue;
            }
            if (!first)
            {
                sb.Append(",");
            }
            sb.Append(cell.nodeNumbering.x);
            first = false;
        }
        sb.Append("}");
        return sb.ToString();
    }

    // collider first: DefenceBase requires the abstract Collider2D, DefenceAttack is auto-added
    DefenceBase NewDefence(string name)
    {
        GameObject defenceObject = new GameObject(name);
        created.Add(defenceObject);
        defenceObject.AddComponent<BoxCollider2D>();
        DefenceBase defence = defenceObject.AddComponent<DefenceBase>();
        return defence;
    }

    List<DefenceBase> NewDefencePool(int count)
    {
        List<DefenceBase> pool = new List<DefenceBase>();
        for (int i = 0; i < count; i++)
        {
            pool.Add(NewDefence("defence_" + i));
        }
        return pool;
    }

    // ------------------------------------------------------------------ Property 1: Bug Condition examples

    [Test]
    public void LaneSight_SoldierBehindEnemyNotInSight()
    {
        SetOccupied(new int[] { 2 });
        Assert.IsFalse(CustomGridSystem.IsSomeoneInSightFromColumn(row, 4), "occupied {2}, minColumn 4");
    }

    [Test]
    public void LaneSight_ShieldBehindEnemiesNotInSight()
    {
        SetOccupied(new int[] { 0, 1, 3 });
        Assert.IsFalse(CustomGridSystem.IsSomeoneInSightFromColumn(row, 5), "occupied {0,1,3}, minColumn 5");
    }

    [Test]
    public void LaneSight_MinColumnPastLastColumnNotInSight()
    {
        SetOccupied(new int[] { 0, 5, 10 });
        Assert.IsFalse(CustomGridSystem.IsSomeoneInSightFromColumn(row, ColumnCount), "occupied {0,5,10}, minColumn " + ColumnCount);
    }

    [Test]
    public void Retarget_NewDefenceInBetweenTakesOver()
    {
        DefenceBase defenceA = NewDefence("defenceA");
        DefenceBase defenceB = NewDefence("defenceB");
        Assert.IsTrue(EnemyAI.ShouldRetarget(defenceA, defenceB.gameObject), "cached A alive, ray hit B");
    }

    // ------------------------------------------------------------------ Property 1: Bug Condition properties

    // design Property 1: rows with only passed enemies are never in sight (Req 2.1, 2.2)
    [Test]
    public void Property1_LaneSightIgnoresPassedEnemies()
    {
        int seed = NewSeed();
        System.Random rng = new System.Random(seed);
        for (int i = 0; i < Iterations; i++)
        {
            int minColumn = rng.Next(1, ColumnCount + 2); // [1, 12]
            int lastPassed = Mathf.Min(minColumn, ColumnCount) - 1;

            // at least one enemy, all left of minColumn
            ClearRow();
            row[rng.Next(0, lastPassed + 1)].isSomeoneOnThisCell = true;
            for (int x = 0; x <= lastPassed; x++)
            {
                if (Chance(rng, 0.4))
                {
                    row[x].isSomeoneOnThisCell = true;
                }
            }

            string context = "seed=" + seed + " iteration=" + i + " occupied=" + Describe(row) + " minColumn=" + minColumn;
            Assert.IsTrue(IsBugConditionLaneSight(row, minColumn), "generator check, " + context);
            Assert.IsFalse(CustomGridSystem.IsSomeoneInSightFromColumn(row, minColumn), context);
        }
    }

    // design Property 1: result equals "some occupied column >= minColumn" (Req 2.1, 2.2, 3.1)
    [Test]
    public void Property1_LaneSightMatchesColumnRule()
    {
        int seed = NewSeed();
        System.Random rng = new System.Random(seed);
        for (int i = 0; i < Iterations; i++)
        {
            RandomOccupancy(rng, rng.NextDouble());
            int minColumn = rng.Next(-2, ColumnCount + 2); // [-2, 12]

            string context = "seed=" + seed + " iteration=" + i + " occupied=" + Describe(row) + " minColumn=" + minColumn;
            Assert.AreEqual(ExpectedInSight(row, minColumn), CustomGridSystem.IsSomeoneInSightFromColumn(row, minColumn), context);
        }
    }

    // design Property 2: a different live defence on the ray always takes over (Req 2.3, 2.4)
    [Test]
    public void Property1_RetargetToFirstDefenceOnRay()
    {
        int seed = NewSeed();
        System.Random rng = new System.Random(seed);
        List<DefenceBase> pool = NewDefencePool(6);
        for (int i = 0; i < Iterations; i++)
        {
            int poolSize = rng.Next(2, 7); // [2, 6]
            int cachedIndex = rng.Next(0, poolSize);
            int hitIndex = rng.Next(0, poolSize - 1);
            if (hitIndex >= cachedIndex)
            {
                hitIndex++;
            }

            string context = "seed=" + seed + " iteration=" + i + " cached=" + cachedIndex + " hit=" + hitIndex;
            Assert.IsTrue(EnemyAI.ShouldRetarget(pool[cachedIndex], pool[hitIndex].gameObject), context);
        }
    }

    // ------------------------------------------------------------------ Property 2: Preservation examples

    [Test]
    public void LaneSight_EnemyAheadStillInSight()
    {
        SetOccupied(new int[] { 2, 7 });
        Assert.IsTrue(CustomGridSystem.IsSomeoneInSightFromColumn(row, 4), "occupied {2,7}, minColumn 4");
    }

    [Test]
    public void LaneSight_OwnCellCounts()
    {
        SetOccupied(new int[] { 4 });
        Assert.IsTrue(CustomGridSystem.IsSomeoneInSightFromColumn(row, 4), "occupied {4}, minColumn 4");
    }

    [Test]
    public void LaneSight_EmptyRowNeverInSight()
    {
        SetOccupied(new int[0]);
        for (int minColumn = -2; minColumn <= ColumnCount + 1; minColumn++)
        {
            Assert.IsFalse(CustomGridSystem.IsSomeoneInSightFromColumn(row, minColumn), "empty row, minColumn " + minColumn);
        }
    }

    [Test]
    public void LaneSight_NegativeMinColumnEqualsFullScan()
    {
        SetOccupied(new int[] { 0 });
        Assert.AreEqual(OriginalRowScan(row), CustomGridSystem.IsSomeoneInSightFromColumn(row, -1), "occupied {0}, minColumn -1");
        SetOccupied(new int[] { 10 });
        Assert.AreEqual(OriginalRowScan(row), CustomGridSystem.IsSomeoneInSightFromColumn(row, -1), "occupied {10}, minColumn -1");
    }

    [Test]
    public void Retarget_SameDefenceKeepsTarget()
    {
        DefenceBase defenceA = NewDefence("defenceA");
        Assert.IsFalse(EnemyAI.ShouldRetarget(defenceA, defenceA.gameObject), "cached A, ray hit A");
    }

    [Test]
    public void Retarget_NullTargetRefills()
    {
        DefenceBase defenceX = NewDefence("defenceX");
        Assert.IsTrue(EnemyAI.ShouldRetarget(null, defenceX.gameObject), "nothing cached");
    }

    [Test]
    public void Retarget_DestroyedTargetRefills()
    {
        DefenceBase defenceA = NewDefence("defenceA");
        DefenceBase defenceX = NewDefence("defenceX");
        UnityEngine.Object.DestroyImmediate(defenceA.gameObject);
        Assert.IsTrue(EnemyAI.ShouldRetarget(defenceA, defenceX.gameObject), "cached A destroyed");
    }

    // ------------------------------------------------------------------ Property 2: Preservation properties

    // design Property 3: outside C_A, lane sight equals the old row-wide scan (Req 3.1, 3.2, 3.12)
    [Test]
    public void Property2_LaneSightPreservedOutsideBugCondition()
    {
        int seed = NewSeed();
        System.Random rng = new System.Random(seed);
        int checkedCases = 0;
        for (int i = 0; i < Iterations; i++)
        {
            RandomOccupancy(rng, rng.NextDouble());
            int minColumn = rng.Next(-2, ColumnCount + 2); // [-2, 12]
            if (IsBugConditionLaneSight(row, minColumn))
            {
                continue;
            }
            checkedCases++;

            string context = "seed=" + seed + " iteration=" + i + " occupied=" + Describe(row) + " minColumn=" + minColumn;
            Assert.AreEqual(OriginalRowScan(row), CustomGridSystem.IsSomeoneInSightFromColumn(row, minColumn), context);
        }
        Assert.Greater(checkedCases, 0, "seed=" + seed + " no cases outside the bug condition");

        // minColumn <= 0 scans the whole row for every occupancy
        for (int i = 0; i < Iterations; i++)
        {
            RandomOccupancy(rng, rng.NextDouble());
            int minColumn = rng.Next(-2, 1); // [-2, 0]

            string context = "seed=" + seed + " iteration=" + i + " occupied=" + Describe(row) + " minColumn=" + minColumn;
            Assert.AreEqual(OriginalRowScan(row), CustomGridSystem.IsSomeoneInSightFromColumn(row, minColumn), context);
        }
    }

    // design Property 4: outside C_B, retargeting equals the old "target == null" rule (Req 3.5, 3.6, 3.12)
    [Test]
    public void Property2_TargetingPreservedOutsideBugCondition()
    {
        int seed = NewSeed();
        System.Random rng = new System.Random(seed);
        List<DefenceBase> pool = NewDefencePool(6);
        DefenceBase destroyed = NewDefence("defenceDestroyed");
        UnityEngine.Object.DestroyImmediate(destroyed.gameObject);

        for (int i = 0; i < Iterations; i++)
        {
            DefenceBase cached = null;
            GameObject hitObject = pool[rng.Next(0, pool.Count)].gameObject;
            string stateName = "null";

            int state = rng.Next(3);
            if (state == 1)
            {
                cached = destroyed;
                stateName = "destroyed";
            }
            else if (state == 2)
            {
                cached = pool[rng.Next(0, pool.Count)];
                hitObject = cached.gameObject;
                stateName = "alive, hit is cached";
            }

            bool expected = (cached == null);
            string context = "seed=" + seed + " iteration=" + i + " cached=" + stateName + " hit=" + hitObject.name;
            Assert.AreEqual(expected, EnemyAI.ShouldRetarget(cached, hitObject), context);
        }
    }
}
