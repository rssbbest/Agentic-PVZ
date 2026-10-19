using System.Collections.Generic;
using System.Collections;
using UnityEngine;
using System.Linq;

public class EnemySpawningHandler : MonoBehaviour
{
    [Header("refrences needed")]
    [SerializeField] CustomGridSystem gridSystem;

    [Header("Enemy Dict")]
    [SerializeField] List<EnemyAI> enemyAIIndexMapping = new List<EnemyAI>(); 
    [SerializeField] EnemyWave enemyWaves;

    [Header("Spawn Settings")]
    [SerializeField] Transform spawnQueue;
    [SerializeField] int gridColumnForEnemies = -1;
    [SerializeField] float interArivalTime;
    [SerializeField] float timeDecreaseRate;

    [Header("Visual settings")]
    [SerializeField] ParticleSystem spawnEffect;

    //[SerializeField] EnemyAI testEnemy;
    float timeLeft = 0;
    int enemyCount;
    bool completedWave = false;

    private void Awake()
    {
        if(gridSystem == null) 
        {
            gridSystem = FindAnyObjectByType<CustomGridSystem>();
        }
    }


    public void InitializeEnemies(string enemiesJson,bool isJson)
    {
        enemyCount = 0;
        completedWave = false;
        if (isJson)
        {
            enemyWaves = JsonUtility.FromJson<EnemyWave>(enemiesJson);
        }

        foreach (EnemySpawnData spawnData in enemyWaves.enemies)
        {
            for (int i = 0; i < spawnData.count; i++)
            {
                EnemyAI enemy = Instantiate(enemyAIIndexMapping[spawnData.enemyId], spawnQueue);
                enemy.gameObject.SetActive(false);
                if (AutopilotBridge.Instance != null) enemy.TrackRun(spawnData.enemyId);
                enemyCount++;
            }
        }

        //Debug.Log(JsonUtility.ToJson(enemyWaves));
    }

    public void StartSpawning()
    {
        timeLeft = interArivalTime;
        if (AutopilotBridge.Managed) Random.InitState(42);
        StartCoroutine(SpawnEnemies());
    }

    IEnumerator SpawnEnemies()
    {
        int enemyCount = EnemyCountInSpawnQueue();
        while (enemyCount >= 1)
        {
            int randomNum = Random.Range(0, enemyCount);
            Vector3 spawnPosition = GetRandomSpawnPosition();

            Instantiate(spawnEffect, spawnPosition,Quaternion.identity);

            yield return new WaitForSeconds(0.3f);

            Transform enemy = spawnQueue.GetChild(randomNum).transform;
            enemy.position = spawnPosition;
            enemy.parent = transform;
            enemy.gameObject.SetActive(true);
            
            
            yield return new WaitForSeconds(timeLeft);
            timeLeft = Mathf.Max(0.05f, timeLeft - timeDecreaseRate);
            enemyCount = EnemyCountInSpawnQueue();
        
        }
        completedWave = true;
    }

    
    Vector3 GetRandomSpawnPosition()
    {
        Vector2Int maxCellCount = gridSystem.GetMaxCellCount();
        if (gridColumnForEnemies == -1)
        {
            gridColumnForEnemies = maxCellCount.x;
        }
        if (gridColumnForEnemies > maxCellCount.x)
        {
            Debug.LogError("INVALID COLUMN FOR ENEMIES SPAWN");
        }

        int randRow = Random.Range(0, maxCellCount.y+1);

        GridCell gridCell = gridSystem.GetCellFromNumbering(gridColumnForEnemies,randRow);

        return gridCell.transform.position;
    }
    public int EnemyCountInSpawnQueue()
    {
        return spawnQueue.childCount;
    }
    public int TotalEnemiesInWave()
    {
        return enemyCount;
    }
    public bool IsWaveCompleted()
    {
        return completedWave && GetComponentsInChildren<EnemyAI>().All(e => e.IsDead());
    }

    //public EnemyAI GetRandomEnemy()
    //{
    //    EnemyAI[] enemyList = GetComponentsInChildren<EnemyAI>();
    //    int rand = Random.Range(0, enemyList.Count());

    //    return enemyList[rand];
    //}
}
