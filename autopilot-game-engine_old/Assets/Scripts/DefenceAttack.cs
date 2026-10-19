using UnityEngine;

public enum AttackType
{
    None,
    slide,
    throwerLane,
    throwerMultiLane
}

public class DefenceAttack : MonoBehaviour
{
    [Header("Attack stats")]
    [SerializeField] AttackType attackType;
    [SerializeField] float      attackDamage;
    [SerializeField] string     enemyTag;
    [SerializeField] Vector3    direction;


    [Header("VisualSettings")]
    [SerializeField] Animator   animator;


    [Header("Sliding type")]
    [SerializeField] float      speed;
    [SerializeField] ParticleSystem hitEffect;

    [Header("ThrowerLane type")]
    [SerializeField] Projectile projectile;
    [SerializeField] Transform  firePoint;
    [SerializeField] float      projectileSpeed;

    [Header("ThrowerArialMultiLane")]
    [SerializeField] float arcHeight=3f;

    bool isAttacking;
    EnemyAI target;

    DefenceBase defBase;

    private void Awake()
    {
        defBase = GetComponent<DefenceBase>();

    }
    void Update()
    {
        if (defBase.GetCurrentHealth() <= 0) return;
        AttackUpdateLoop();
    }

    void AttackUpdateLoop()
    {
        if (isAttacking && AttackType.slide == attackType)
        {
            transform.position += direction.normalized * speed * Time.deltaTime;
        }
    }

    void OnTriggerEnter2D(Collider2D collision)
    {
        if (defBase.GetCurrentHealth() <= 0) return;
        if(AttackType.slide == attackType)
        {
            if (collision.gameObject.tag == enemyTag)
            {
                EnemyAI enemy = collision.gameObject.GetComponent<EnemyAI>();
                Instantiate(hitEffect, collision.transform.position, Quaternion.identity);
                enemy.TakeDamage(attackDamage);

                // logging 
                LoggingSystem.LogDefenceDamage(gameObject.GetInstanceID().ToString(), defBase.myCell, attackDamage);
            }
        }
        
    }

    public void SetTriggerAttack(bool state)
    {

        isAttacking = state;
        animator.SetBool("isAttacking", isAttacking);
        
    }


    public void SetTriggerScared(bool state)
    {

        isAttacking = state;
        animator.SetBool("isScared", isAttacking);

    }

    public void SetTriggerMultiLaneAttack(bool state,EnemyAI targetG)
    {

        isAttacking = state;
        animator.SetBool("isAttacking", isAttacking);
        target = targetG;

    }

    public void ThrowerLaneProjectile()
    {
        if (defBase.GetCurrentHealth() <= 0) return;
        Projectile prop = Instantiate(projectile, firePoint.position, Quaternion.identity);
        prop.SetAttributes(projectileSpeed > 0 ? projectileSpeed : speed, attackDamage, direction, defBase,null);
        prop.Throw();
    }
    public void ThrowerMultiLaneProjectile()
    {
        if (defBase.GetCurrentHealth() <= 0) return;
        if (target == null || target.IsDead()) return;
        Projectile prop = Instantiate(projectile, firePoint.position, Quaternion.identity);
        prop.SetAttributes(projectileSpeed > 0 ? projectileSpeed : speed, attackDamage,arcHeight,target, defBase); // enemy lock in left
        prop.Throw();
    }
}
