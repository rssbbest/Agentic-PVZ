using System.Collections.Generic;
using System.Collections;
using UnityEngine;

public class PlacementSystem : MonoBehaviour
{
    [Header("System Settings")]
    [SerializeField] CustomGridSystem gridSystem;
    [SerializeField] List<DefenceBase> defenceIndexMapping = new List<DefenceBase>();


    [SerializeField] DefencePlacementDataWrapper defencePlacementData;

    int maxDefenceCount;

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
        maxDefenceCount = 0;
        foreach(DefencePlacementCard defenceData in defencePlacementData.defencePlacementData)
        {
            GridCell cell = gridSystem.GetCellFromNumbering(defenceData.defenceGridPosition.x, defenceData.defenceGridPosition.y);
            Vector3 position = cell.transform.position;
            DefenceBase defence = Instantiate(defenceIndexMapping[defenceData.defenceId], position, Quaternion.identity, transform);
            defence.myCell = cell.nodeNumbering;
            defence.runDefenceKind = defenceData.defenceId;
            maxDefenceCount++;
        }
    }
    public int DefenceCount()
    {
        return transform.childCount;
    }
    public int MaxDefenceCount()
    {
        return maxDefenceCount;
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


