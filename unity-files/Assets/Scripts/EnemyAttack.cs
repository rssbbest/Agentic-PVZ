using UnityEngine;

public enum EnemyType
{
    Melee,
    Thrower,
    Lazer,
    SucideBomber,
}

public class EnemyAttack : MonoBehaviour
{
    
    [Header("Attack Stats")]
    [SerializeField] EnemyType enemyType;
    [SerializeField] float     attackDamage;


    [Header("ProjectileType")]
    [SerializeField] float      projectileSpeed;
    [SerializeField] Projectile projectile;
    [SerializeField] Transform  throwPoint;

    [Header("LazerType")]
    [SerializeField] LineRenderer laserPoint;
    [SerializeField] float        laserOffDelay = 0.25f; // NEW: grace time before laser turns off


    [Header("SucideBomberType")]
    [SerializeField] float blastRadius;
    [SerializeField] ParticleSystem blastEffect;

    EnemyAI enemyAI;
    bool laserState;
    float laserOffTimer; // NEW: time without attack state while laser is on

    private void Awake()
    {
        enemyAI = GetComponent<EnemyAI>();
    }
    private void Update()
    {
        
        // UPDATED: turn laser off only after attack state is lost for laserOffDelay
        if (enemyType == EnemyType.Lazer && laserState)
        {
            if (enemyAI.GetCurrentAttackState())
            {
                laserOffTimer = 0f;
            }
            else
            {
                laserOffTimer += Time.deltaTime;
            }
            if (laserOffTimer >= laserOffDelay)
            {
                SetLaserOFF();
            }
        }
    }


    private void OnDisable()
    {
        if(enemyType == EnemyType.SucideBomber && enemyAI.GetCurrentHealth()<=0)
        {
            Instantiate(blastEffect, transform.position, Quaternion.identity);
            Collider2D[] hits = Physics2D.OverlapCircleAll(transform.position,blastRadius,enemyAI.GetTargetLayerMask());
            foreach(Collider2D hit in hits)
            {
                if(hit.gameObject.tag == enemyAI.GetTargetTag())
                {
                    DefenceBase defenceBase = hit.GetComponent<DefenceBase>();

                    LoggingSystem.LogEnemyDamage(enemyAI.name, attackDamage);
                    defenceBase.TakeDamage(attackDamage);
                }
            }
        }
    }



    public void ThrowProjectile()
    {
        Projectile prop = Instantiate(projectile, throwPoint.position, new Quaternion(0, 0, 0.923879564f, 0.382683426f));
        prop.SetAttributes(projectileSpeed, attackDamage, enemyAI.directionOfAttack,null,enemyAI);
        prop.Throw();
    }

    public void SetLaserON()
    {
        laserState = true;
        laserOffTimer = 0f; // NEW: reset grace timer
        laserPoint.enabled = true;
        //Debug.Log("laser turned on");
    }
    public void SetLaserOFF()
    {
        laserState = false;
        laserPoint.SetPositions(new Vector3[3]);
        laserPoint.enabled = false;
        //Debug.Log("laser turned off");


    }

    public void DoDamageLazer(DefenceBase defencebase)
    {
        if(enemyType == EnemyType.Lazer && laserState)
        {
            defencebase.TakeDamage(attackDamage * Time.deltaTime);

            LoggingSystem.LogEnemyDamage(enemyAI.name, attackDamage * Time.deltaTime);

            Vector3[] vertexPoints = new Vector3[2];
            vertexPoints[0] = laserPoint.transform.position;
            vertexPoints[1] = defencebase.transform.position;
            laserPoint.SetPositions(vertexPoints);
        }
        else if(enemyType == EnemyType.Lazer && laserState == false)
        {
            // NEW: attack loop has no laser event, so re-enable laser from code
            Animator animator = enemyAI.GetAnimator();
            if (!animator.IsInTransition(0) && animator.GetCurrentAnimatorStateInfo(0).IsName("attack"))
            {
                SetLaserON();
            }
            else
            {
                SetLaserOFF();
            }
        }
    }
    // NEW: suicide bomber blows up as soon as it reaches a defence
    public void DoDamageBomber()
    {
        if (enemyType == EnemyType.SucideBomber)
        {
            enemyAI.SelfDestruct();
        }
    }
    public void DoDamage(DefenceBase defencebase)
    {
        defencebase.TakeDamage(attackDamage);
        LoggingSystem.LogEnemyDamage(enemyAI.name, attackDamage);

    }

    private void OnDrawGizmosSelected()
    {
        Gizmos.color = Color.black;
        Gizmos.DrawWireSphere(transform.position, blastRadius);
    }

}
