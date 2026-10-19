using System.Collections.Generic; // NEW: list of enemies on this cell
using TMPro;
using UnityEngine;

public class GridCell : MonoBehaviour
{
    static public float          nodeSize = 1.5f;
    public Vector2Int            nodeNumbering;
    [SerializeField] TextMeshPro label;
    [SerializeField] LayerMask   detectionLayerMask;

    public bool isSomeoneOnThisCell;
    // NEW: every enemy currently overlapping this cell
    List<EnemyAI> enemiesOnCell = new List<EnemyAI>();

    void OnTriggerEnter2D(Collider2D collision)
    {
        if ((detectionLayerMask.value & (1 << collision.gameObject.layer)) != 0)
        {
            // NEW: add the enemy once instead of overwriting the previous one
            EnemyAI enemy = collision.GetComponent<EnemyAI>();
            if (enemy != null && !enemiesOnCell.Contains(enemy))
            {
                enemiesOnCell.Add(enemy);
            }
            isSomeoneOnThisCell = enemiesOnCell.Count > 0;
        }
    }
    private void OnTriggerExit2D(Collider2D collision)
    {
        if ((detectionLayerMask.value & (1 << collision.gameObject.layer)) != 0)
        {
            // NEW: remove only the enemy that left
            EnemyAI enemy = collision.GetComponent<EnemyAI>();
            enemiesOnCell.Remove(enemy);
            isSomeoneOnThisCell = enemiesOnCell.Count > 0;
        }
    }

    // NEW: live enemy first, dying enemy as fallback, null if empty
    public EnemyAI GetTarget()
    {
        EnemyAI dyingEnemy = null;
        foreach (EnemyAI enemy in enemiesOnCell)
        {
            if (enemy == null || !enemy.gameObject.activeInHierarchy)
            {
                continue;
            }
            if (!enemy.IsDead())
            {
                return enemy;
            }
            if (dyingEnemy == null)
            {
                dyingEnemy = enemy;
            }
        }
        return dyingEnemy;
    }

    public void UpdateLabel(int x,int y)
    {
        nodeNumbering = new Vector2Int(x,y);
        label.text = $"({x},{y})";
    }
}
