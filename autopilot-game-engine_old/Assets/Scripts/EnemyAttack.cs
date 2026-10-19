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


    [Header("SucideBomberType")]
    [SerializeField] float blastRadius;
    [SerializeField] ParticleSystem blastEffect;

    EnemyAI enemyAI;
    bool laserState;

    private void Awake()
    {
        enemyAI = GetComponent<EnemyAI>();
    }
    private void Update()
    {
        
        if (!enemyAI.GetCurrentAttackState() && enemyType == EnemyType.Lazer && laserState)
        {
            SetLaserOFF();
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

                    LoggingSystem.LogEnemyDamage(enemyAI.gameObject.GetInstanceID().ToString(), attackDamage);
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

            LoggingSystem.LogEnemyDamage(enemyAI.gameObject.GetInstanceID().ToString(), attackDamage * Time.deltaTime);

            Vector3[] vertexPoints = new Vector3[2];
            vertexPoints[0] = laserPoint.transform.position;
            vertexPoints[1] = defencebase.transform.position;
            laserPoint.SetPositions(vertexPoints);
        }
        else if(enemyType == EnemyType.Lazer && laserState == false)
        {
            SetLaserOFF();
        }
    }
    public void DoDamage(DefenceBase defencebase)
    {
        defencebase.TakeDamage(attackDamage);
        LoggingSystem.LogEnemyDamage(enemyAI.gameObject.GetInstanceID().ToString(), attackDamage);

    }

    private void OnDrawGizmosSelected()
    {
        Gizmos.color = Color.black;
        Gizmos.DrawWireSphere(transform.position, blastRadius);
    }

}
