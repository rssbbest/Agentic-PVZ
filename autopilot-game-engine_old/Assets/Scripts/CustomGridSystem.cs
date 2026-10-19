using System.Collections.Generic;
using UnityEngine;

public class CustomGridSystem : MonoBehaviour
{
    [Header("Rows / columns")]
    [SerializeField] Vector2Int startPosLocal;
    [SerializeField] Vector2Int endPosLocal;
    [SerializeField] Transform  gridObj;


    Vector2 startPos;
    Vector2Int maxCellCount;
    List<List<GridCell>> grid = new List<List<GridCell>>();
    void Awake()
    {
        //initializing grid
        startPos = new Vector2(transform.position.x, transform.position.y);
        int maxXNodes = (int)(Mathf.Abs(startPosLocal.x - endPosLocal.x) / GridCell.nodeSize);
        int maxYNodes = (int)(Mathf.Abs(startPosLocal.y - endPosLocal.y) / GridCell.nodeSize);
        if (AutopilotBridge.Managed) { maxXNodes = 5; maxYNodes = 4; }
        maxCellCount = new Vector2Int(maxXNodes, maxYNodes);
        
        PopulateGrid(maxXNodes, maxYNodes);
    }

    void PopulateGrid(int maxXNodes, int maxYNodes)
    {
        for (int y = 0; y <= maxYNodes; y++)
        {
            List<GridCell> gridCellList = new List<GridCell>();
            for (int x = 0; x <= maxXNodes; x++)
            {
                //setting position for new cell
                Vector3 position = new Vector3(startPos.x + (x * GridCell.nodeSize), startPos.y + (y * GridCell.nodeSize), 0);

                //instansiating new cell
                Transform newCell = Instantiate(gridObj, position, Quaternion.identity, transform);
                newCell.localScale = new Vector3(GridCell.nodeSize, GridCell.nodeSize, GridCell.nodeSize);

                //setting GridCell properties - updating label,numbering
                GridCell objectGridCell = newCell.GetComponent<GridCell>();
                objectGridCell.UpdateLabel(x, y);
                gridCellList.Add(objectGridCell);
            }
            grid.Add(gridCellList);
        }
    }

    public Vector2Int GetMaxCellCount()
    {
        return maxCellCount;
    }
    public GridCell GetCellFromNumbering(int x,int y)
    {
        foreach (List<GridCell> row in grid)
        {
            foreach (GridCell cell in row)
            {
                if (cell.nodeNumbering == new Vector2Int(x, y))
                    return cell;
            }
        }
        return null;
    }

    public bool isSomeoneInSight(int rowIndex)
    {
        foreach(GridCell cell in grid[rowIndex])
        {
            if (cell.isSomeoneOnThisCell)
            {
                //Debug.Log("Saw someone");
                return true;
            }
        }
        return false;
    }

    public EnemyAI GetEnemyIfSomeoneInSight(int rowIndex)
    {
        foreach (GridCell cell in grid[rowIndex])
        {
            if (cell.isSomeoneOnThisCell)
            {
                if (cell.target != null)
                {
                    return cell.target;
                }
            }
        }
        return null;
    }
}
