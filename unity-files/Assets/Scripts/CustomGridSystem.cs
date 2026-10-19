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

    // NEW: O(1) nearest cell for moving units (enemies have no myCell)
    public Vector2Int GetNearestCellNumbering(Vector3 worldPosition)
    {
        Vector2 flatPosition = new Vector2(worldPosition.x, worldPosition.y);
        return NearestCellNumbering(flatPosition, startPos, GridCell.nodeSize, maxCellCount);
    }

    // NEW: inverts the PopulateGrid placement formula, clamped into the grid
    public static Vector2Int NearestCellNumbering(Vector2 worldPosition, Vector2 origin, float cellSize, Vector2Int maxCells)
    {
        if (cellSize <= 0f)
        {
            Debug.LogWarning("[Grid] Invalid cell size, nearest cell defaults to (0,0)");
            return Vector2Int.zero;
        }

        int x = Mathf.RoundToInt((worldPosition.x - origin.x) / cellSize);
        int y = Mathf.RoundToInt((worldPosition.y - origin.y) / cellSize);
        x = Mathf.Clamp(x, 0, maxCells.x);
        y = Mathf.Clamp(y, 0, maxCells.y);
        return new Vector2Int(x, y);
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

    // NEW: lane sight that skips enemies left of minColumn (already passed)
    public bool isSomeoneInSight(int rowIndex, int minColumn)
    {
        return IsSomeoneInSightFromColumn(grid[rowIndex], minColumn);
    }

    // NEW: pure row scan from minColumn rightwards, testable without a scene
    public static bool IsSomeoneInSightFromColumn(List<GridCell> row, int minColumn)
    {
        foreach (GridCell cell in row)
        {
            // NEW: skip cells left of minColumn (already passed)
            if (cell.nodeNumbering.x < minColumn)
            {
                continue;
            }
            if (cell.isSomeoneOnThisCell)
            {
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
                // NEW: ask the cell for its best current target
                EnemyAI cellTarget = cell.GetTarget();
                if (cellTarget != null)
                {
                    return cellTarget;
                }
            }
        }
        return null;
    }
}
