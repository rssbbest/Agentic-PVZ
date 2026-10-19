using System.Collections.Generic;
using UnityEngine;

[System.Serializable]
public class EnemySpawnData
{
    public int enemyId;
    public int     count;
    public List<int> allowedRows; // NEW: rows this type can spawn in, empty = all rows
}