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
    [SerializeField] float minInterArrivalTime = 0f; // NEW: floor for the wait between spawns

    [Header("Visual settings")]
    [SerializeField] ParticleSystem spawnEffect;

    //[SerializeField] EnemyAI testEnemy;
    float timeLeft = 0;
    int enemyCount;
    bool completedWave = false;
    bool firstSpawn = false;
    Dictionary<Transform, List<int>> rowsByEnemy = new Dictionary<Transform, List<int>>(); // NEW: allowed rows per queued enemy, null = all rows
    Dictionary<Transform, int> idByEnemy = new Dictionary<Transform, int>(); // NEW: enemyId per queued enemy
    int[] spawnSequence; // NEW: backend spawn order, null/empty = random
    int sequenceIndex = 0; // NEW: next entry in spawnSequence
    System.Random spawnRng; // NEW: the level's seeded generator (set by ApplySpawnSettings)

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
        Dictionary<string, int> typeCounters = new Dictionary<string, int>(); // NEW: per-prefab counter for unique names
        rowsByEnemy.Clear(); // NEW
        idByEnemy.Clear(); // NEW
        int maxRow = gridSystem.GetMaxCellCount().y; // NEW
        if (isJson)
        {
            enemyWaves = JsonUtility.FromJson<EnemyWave>(enemiesJson);
        }

        foreach (EnemySpawnData spawnData in enemyWaves.enemies)
        {
            // NEW: resolve allowed rows once per entry
            List<int> rows = SpawnRules.ResolveRows(spawnData.allowedRows, maxRow, enemyAIIndexMapping[spawnData.enemyId].name);

            for (int i = 0; i < spawnData.count; i++)
            {
                
                EnemyAI enemy = Instantiate(enemyAIIndexMapping[spawnData.enemyId], spawnQueue);
                // NEW: unique name "<PrefabName>_<n>" for per-unit logging
                string prefabName = enemyAIIndexMapping[spawnData.enemyId].name;
                int n = 0;
                if (typeCounters.ContainsKey(prefabName))
                {
                    n = typeCounters[prefabName];
                }
                enemy.name = prefabName + "_" + n;
                typeCounters[prefabName] = n + 1;
                rowsByEnemy[enemy.transform] = rows; // NEW
                idByEnemy[enemy.transform] = spawnData.enemyId; // NEW
                enemy.gameObject.SetActive(false);
                enemyCount++;

                if (!firstSpawn)
                {
                    
                    firstSpawn = true;
                    //GameManager.Instance.CallReinforcementsFORCEFULLY();
                }
            }
        }

        //Debug.Log(JsonUtility.ToJson(enemyWaves));
    }

    // NEW: overlay backend "spawnSettings" onto inspector values (call before StartSpawning)
    public void ApplySpawnSettings(string levelJson)
    {
        SpawnSettings defaults = new SpawnSettings();
        defaults.interArrivalTime = interArivalTime;
        defaults.timeDecreaseRate = timeDecreaseRate;
        defaults.minInterArrivalTime = minInterArrivalTime;

        SpawnSettings settings = SpawnRules.ParseSpawnSettings(levelJson, defaults);
        interArivalTime = settings.interArrivalTime;
        timeDecreaseRate = settings.timeDecreaseRate;
        minInterArrivalTime = settings.minInterArrivalTime;
        spawnSequence = settings.spawnSequence; // NEW
        spawnRng = SpawnRules.SeededRandom(settings.seed); // NEW: same rows and order in every game of this level
    }

    public void StartSpawning()
    {
        timeLeft = interArivalTime;
        sequenceIndex = 0; // NEW
        StartCoroutine(SpawnEnemies());
    }

    IEnumerator SpawnEnemies()
    {
        bool openingPending = true;
        int enemyCount = EnemyCountInSpawnQueue();
        while (enemyCount >= 1)
        {
            Transform enemy = PickNextEnemy(enemyCount); // UPDATED: sequence first, then random; picked before row so its allowed rows are known

            // NEW: row comes from this enemy's allowed rows
            List<int> rows = null;
            rowsByEnemy.TryGetValue(enemy, out rows);
            Vector3 spawnPosition = GetRandomSpawnPosition(rows);

            Instantiate(spawnEffect, spawnPosition,Quaternion.identity);

            yield return new WaitForSeconds(0.3f);

            enemy.position = spawnPosition;
            enemy.parent = transform;
            enemy.gameObject.SetActive(true);
            if (openingPending)
            {
                openingPending = false;
                yield return GameManager.Instance.OpeningAfterFirstSpawn();
            }
            
            yield return new WaitForSeconds(SpawnRules.NextWait(timeLeft, minInterArrivalTime)); // UPDATED: wait floor
            timeLeft -= timeDecreaseRate;
            enemyCount = EnemyCountInSpawnQueue();
        
        }

        // NEW: wave completes only after every spawned enemy is gone
        while (ActiveEnemyCount() > 0)
        {
            yield return null;
        }
        completedWave = true;
    }

    // NEW: next queued enemy from spawnSequence, random once sequence is missing or used up
    Transform PickNextEnemy(int queueCount)
    {
        if (spawnSequence != null && sequenceIndex < spawnSequence.Length)
        {
            int wantedId = spawnSequence[sequenceIndex];
            sequenceIndex++;
            for (int i = 0; i < queueCount; i++)
            {
                Transform queued = spawnQueue.GetChild(i);
                int queuedId;
                if (idByEnemy.TryGetValue(queued, out queuedId) && queuedId == wantedId)
                {
                    return queued;
                }
            }
        }

        int randomNum = SpawnRules.Pick(queueCount, spawnRng); // UPDATED: seeded when the level has a seed
        return spawnQueue.GetChild(randomNum);
    }

    // NEW: spawned enemies still active under spawner (queued ones are inactive, so skipped)
    public int ActiveEnemyCount()
    {
        EnemyAI[] activeEnemies = GetComponentsInChildren<EnemyAI>();
        return activeEnemies.Length;
    }

    // NEW: true if a living spawned enemy is at or left of maxColumn (enemies walk left toward the base)
    public bool IsEnemyNearBase(int maxColumn)
    {
        EnemyAI[] activeEnemies = GetComponentsInChildren<EnemyAI>();
        foreach (EnemyAI enemy in activeEnemies)
        {
            if (enemy.IsDead())
            {
                continue;
            }
            Vector2Int cell = gridSystem.GetNearestCellNumbering(enemy.transform.position);
            if (cell.x <= maxColumn)
            {
                return true;
            }
        }
        return false;
    }

    // NEW: true if a walking enemy with no defence ahead in its row reaches the base within maxSeconds (game time)
    public bool IsBreakthroughImminent(float maxSeconds, PlacementSystem placementSystem)
    {
        float baseX = gridSystem.transform.position.x; // column 0
        EnemyAI[] activeEnemies = GetComponentsInChildren<EnemyAI>();
        foreach (EnemyAI enemy in activeEnemies)
        {
            // dying, or already stopped by a defence
            if (enemy.IsDead() || enemy.GetCurrentAttackState())
            {
                continue;
            }
            float speed = enemy.GetMovementSpeed();
            if (speed <= 0f)
            {
                continue;
            }
            Vector2Int cell = gridSystem.GetNearestCellNumbering(enemy.transform.position);
            if (placementSystem.HasDefenceAhead(cell.y, cell.x))
            {
                continue;
            }
            float secondsToBase = (enemy.transform.position.x - baseX) / speed;
            if (secondsToBase <= maxSeconds)
            {
                return true;
            }
        }
        return false;
    }

    
    Vector3 GetRandomSpawnPosition(List<int> validRows) // UPDATED: validRows, null = all rows
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

        int randRow = SpawnRules.PickRow(validRows, maxCellCount.y, spawnRng); // UPDATED: allowed rows, seeded per level

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
        return completedWave;
    }

    //public EnemyAI GetRandomEnemy()
    //{
    //    EnemyAI[] enemyList = GetComponentsInChildren<EnemyAI>();
    //    int rand = Random.Range(0, enemyList.Count());

    //    return enemyList[rand];
    //}
}
