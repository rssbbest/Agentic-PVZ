using System.Collections.Generic;
using System.Collections;
using UnityEngine;

public class PlacementSystem : MonoBehaviour
{
    [Header("System Settings")]
    [SerializeField] CustomGridSystem gridSystem;
    [SerializeField] List<DefenceBase> defenceIndexMapping = new List<DefenceBase>();


    [SerializeField] DefencePlacementDataWrapper defencePlacementData;

    PlacementLedger ledger = new PlacementLedger(); // UPDATED: run-wide names + count (replaces maxDefenceCount)

    private void Awake()
    {
        if (gridSystem == null) { gridSystem = FindAnyObjectByType<CustomGridSystem>(); }
    }
    //private void Start()
    //{

    //    //Debug.Log(JsonUtility.ToJson(defencePlacementData));
    //}

    public void ConvertDefencePlacementJsonToObject(string json)
    {
        defencePlacementData = JsonUtility.FromJson<DefencePlacementDataWrapper>(json);
    }

    public void InitializeDefences()
    {
        foreach(DefencePlacementCard defenceData in defencePlacementData.defencePlacementData)
        {
            PlaceDefence(defenceData); // UPDATED: shared single-card helper
        }
    }

    // NEW: extra batch mid-run, same JSON schema as start-of-run; appends, never replaces
    public int PlaceDefencesMidRun(string defencePlacementJson)
    {
        if (string.IsNullOrWhiteSpace(defencePlacementJson))
        {
            return 0;
        }
        DefencePlacementDataWrapper batch = JsonUtility.FromJson<DefencePlacementDataWrapper>(defencePlacementJson);
        if (batch == null || batch.defencePlacementData == null)
        {
            return 0;
        }

        int placed = 0;
        foreach (DefencePlacementCard card in batch.defencePlacementData)
        {
            PlaceDefence(card);
            placed++;
        }
        Debug.Log("[Placement] Mid-run batch placed: " + placed);
        return placed;
    }

    // NEW: one card -> one defence, used by both paths so names and counts stay run-wide
    DefenceBase PlaceDefence(DefencePlacementCard card)
    {
        GridCell cell = gridSystem.GetCellFromNumbering(card.defenceGridPosition.x, card.defenceGridPosition.y);
        Vector3 position = cell.transform.position;
        DefenceBase prefab = defenceIndexMapping[card.defenceId];
        DefenceBase defence = Instantiate(prefab, position, Quaternion.identity, transform);
        defence.myCell = cell.nodeNumbering;
        defence.name = ledger.RegisterPlacement(prefab.name); // unique "<PrefabName>_<n>" for per-unit logging
        return defence;
    }

    public int DefenceCount()
    {
        return transform.childCount;
    }
    public int MaxDefenceCount()
    {
        return ledger.PlacedCount(); // UPDATED: start-of-run + mid-run
    }

    // NEW: true if a defence in this row sits closer to the base than column (it still blocks the lane)
    public bool HasDefenceAhead(int row, int column)
    {
        DefenceBase[] defences = GetComponentsInChildren<DefenceBase>();
        foreach (DefenceBase defence in defences)
        {
            if (defence.myCell.y == row && defence.myCell.x < column)
            {
                return true;
            }
        }
        return false;
    }

    public bool isARowClearOfDefences()
    {
        List<int> rowCount = new List<int>();
        for(int i = 0; i<= gridSystem.GetMaxCellCount().y ; i++)
        {
            rowCount.Add(i);
        }
        DefenceBase[] children = transform.GetComponentsInChildren<DefenceBase>();

        foreach (DefenceBase defence in children)
        {
            if (rowCount.Contains(defence.myCell.y))
            {
                rowCount.Remove(defence.myCell.y);
            }
        }
        return !(rowCount.Count==0);
    }

}


