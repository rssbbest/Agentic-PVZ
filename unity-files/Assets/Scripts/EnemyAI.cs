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
        // deltaTime can still describe the frame before a synchronous opening freeze.
        if (Time.timeScale == 0f) return;
        Movement();
        CheckCurrentHealth();
        Regeneratehealth();
    }

    private void Regeneratehealth()
    {
        if(!canRegeneratehealth || isDead) return; // UPDATED: no regen while dying
        if (health < maxHealth)
        {
            health += regenerateRatePerSec * Time.deltaTime;
            healthBar.UpdateHealth(health, maxHealth);
        }
    }

    private void Movement()
    {
        // NEW: dying enemies stop targeting and attacking
        if (isDead)
        {
            return;
        }

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
                
                
                if (ShouldRetarget(target, hit.collider.gameObject)) // UPDATED: also retarget when a different defence blocks the ray
                {
                    //Debug.Log(hit.collider.gameObject.name);
                    target = hit.collider.gameObject.GetComponent<DefenceBase>();
                }
                enemyAttack.DoDamageLazer(target);
                enemyAttack.DoDamageBomber(); // NEW: suicide bomber explodes on contact


                return;
            }
            else
            {
                target = null;
            }
        
        }
        attackState = false;
        if (!isDead)
        {
            transform.position += directionOfAttack * movementSpeed * Time.deltaTime;
        }
    }
    // NEW: retarget when nothing is cached (or it was destroyed) or the ray hit another defence
    public static bool ShouldRetarget(DefenceBase cachedTarget, GameObject hitObject)
    {
        if (cachedTarget == null)
        {
            return true;
        }
        // NEW: a different defence blocks the ray
        if (cachedTarget.gameObject != hitObject)
        {
            return true;
        }
        return false;
    }

    private void CheckCurrentHealth()
    {
        if (health<= 0){
            TriggerDeathSequence();
        }
    }

    private void TriggerDeathSequence()
    {
        // NEW: log once, at the moment of death
        if (isDead)
        {
            return;
        }
        LoggingSystem.LogEnemyKilled(transform.name);
        animator.SetBool("isDead", true);
        isDead = true;
        attackState = false; // NEW: laser switches off, snapshot stops reporting an attack
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

    // NEW: max health for state snapshot
    public float GetMaxHealth()
    {
        return maxHealth;
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
    public bool IsDead()
    {
        return isDead;
    }
    // NEW: die now (suicide bomber on contact); the blast runs in EnemyAttack.OnDisable
    public void SelfDestruct()
    {
        health = 0f;
        healthBar.UpdateHealth(health, maxHealth);
        TriggerDeathSequence();
    }
    // NEW: walk speed for the close-call time-to-base estimate
    public float GetMovementSpeed()
    {
        return movementSpeed;
    }
    // NEW: animator access for laser state sync
    public Animator GetAnimator()
    {
        return animator;
    }
}
