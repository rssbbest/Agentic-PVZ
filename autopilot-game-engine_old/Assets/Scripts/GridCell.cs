using TMPro;
using UnityEngine;

public class GridCell : MonoBehaviour
{
    static public float          nodeSize = 1.5f;
    public Vector2Int            nodeNumbering;
    [SerializeField] TextMeshPro label;
    [SerializeField] LayerMask   detectionLayerMask;

    public bool isSomeoneOnThisCell;
    public EnemyAI target;

    void OnTriggerEnter2D(Collider2D collision)
    {
        if ((detectionLayerMask.value & (1 << collision.gameObject.layer)) != 0)
        {
            isSomeoneOnThisCell = true;
            target = collision.GetComponent<EnemyAI>();

        }
    }
    private void OnTriggerExit2D(Collider2D collision)
    {
        if ((detectionLayerMask.value & (1 << collision.gameObject.layer)) != 0)
        {
            isSomeoneOnThisCell = false;
            target = null;
        }
    }

    public void UpdateLabel(int x,int y)
    {
        nodeNumbering = new Vector2Int(x,y);
        label.text = $"({x},{y})";
    }
}
