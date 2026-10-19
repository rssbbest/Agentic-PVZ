using UnityEngine;



[RequireComponent(typeof(Rigidbody2D))]
[RequireComponent(typeof(EnemyAttack))]
[RequireComponent(typeof(Collider2D))]
public class EnemyAI : MonoBehaviour
{
    [Header("Enemy stats Attack")]
    [SerializeField] float    movementSpeed;
    [SerializeField] float    attackRange;

    [Header("Enemy stats Defence")]
    [SerializeField] float    maxHealth;
    [SerializeField] float    regenerateRatePerSec;
    [SerializeField] bool     canRegeneratehealth = false;



    [Header("AI settings")]
    [SerializeField] LayerMask targetLayers;
    [SerializeField] string    defenceTag= "defence";
    [SerializeField] EnemyAttack enemyAttack;
    public Vector3             directionOfAttack;



    [Header("Visuals setting")]
    [SerializeField] Animator  animator;
    [SerializeField] HealthBar healthBar;
    //[SerializeField] ParticleSystem spawnEffect;
    //[SerializeField] GameObject visuals;

    float       health;
    bool        attackState = false;
    bool        isDead = false;
    DefenceBase target;
    public int runEnemyKind;
    public float MaxHealth => maxHealth;
    bool trackedRun;
    bool resolved;

    public void TrackRun(int kind)
    {
        runEnemyKind = kind;
        trackedRun = true;
        resolved = false;
        AutopilotBridge.Instance?.Register(this);
    }

    void OnDisable()
    {
        if (trackedRun && !resolved && health <= 0) ResolveRun(true);
    }

    void OnDestroy()
    {
        if (trackedRun && !resolved) ResolveRun(health <= 0);
    }

    void ResolveRun(bool dead)
    {
        resolved = true;
        AutopilotBridge.Instance?.EnemyResolved(dead);
    }

    private void Start()
    {
        health = maxHealth;
    }
    private void OnEnable()
    {
        // reseting state for next cycle of zombie
        health = maxHealth;
        attackState = false;
        isDead = false;
        animator.SetBool("isAttacking", false);
        animator.SetBool("isDead", false);
        //StartCoroutine(SpawnSequence());
    }
    //IEnumerator SpawnSequence()
    //{
    //    visuals.SetActive(false);
    //    spawnEffect.Play();
    //    yield return new WaitForSeconds(0.1f);
    //    visuals.SetActive(true);
    //}

    // Update is called once per frame
    void Update()
    {
        CheckCurrentHealth();
        if (isDead) return;
        Movement();
        Regeneratehealth();
    }

    private void Regeneratehealth()
    {
        if(!canRegeneratehealth) return;
        if (health < maxHealth)
        {
            health += regenerateRatePerSec * Time.deltaTime;
            healthBar.UpdateHealth(health, maxHealth);
        }
    }

    private void Movement()
    {
        //set animation bool variables
        animator.SetBool("isAttacking", attackState);

        //raycast for target check
        RaycastHit2D hit = Physics2D.Raycast(transform.position, directionOfAttack, attackRange,targetLayers);
        if (hit.collider != null)
        {
            //if found target
            if (hit.collider.gameObject.tag == defenceTag)
            {
                //Debug.Log("Hit: defence ");
                attackState = true;


                //debug code
                //TakeDamage(30 * Time.deltaTime);
                
                
                if (target == null || target.gameObject != hit.collider.gameObject)
                {
                    //Debug.Log(hit.collider.gameObject.name);
                    target = hit.collider.gameObject.GetComponent<DefenceBase>();
                }
                enemyAttack.DoDamageLazer(target);


                return;
            }
            else
            {
                target = null;
            }
        
        }
        target = null;
        attackState = false;
        if (!isDead)
        {
            transform.position += directionOfAttack * movementSpeed * Time.deltaTime;
        }
    }
    private void CheckCurrentHealth()
    {
        if (health<= 0){
            TriggerDeathSequence();
        }
    }

    private void TriggerDeathSequence()
    {
        if (trackedRun && !resolved) ResolveRun(true);
        if (isDead) return;
        LoggingSystem.LogEnemyKilled(gameObject.GetInstanceID().ToString());
        animator.SetBool("isDead", true);
        isDead = true;
    }
    public void SetPosition(Vector2 position)
    {
        health = maxHealth;
        transform.position = new Vector3 (position.x, position.y,0);
    }

    public void TakeDamage(float damage)
    {
        health-= damage;
        healthBar.UpdateHealth(health, maxHealth);
    }

    public void EnemyAttackEvent()
    {
        if (attackState && target != null)
        {
            enemyAttack.DoDamage(target);
        }
    }

    public float GetCurrentHealth()
    {
        return health;
    }


    public bool GetCurrentAttackState()
    {
        return attackState;
    }

    public LayerMask GetTargetLayerMask()
    {
        return targetLayers;
    }
    public string GetTargetTag()
    {
        return defenceTag;
    }
    public bool IsDead() => isDead;
}
