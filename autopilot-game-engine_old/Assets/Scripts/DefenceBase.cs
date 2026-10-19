using System;
using UnityEngine;


public enum AttackTriggerType{
    OnPoked,
    OnSight,
    OnSightMultiLane,
    Static
}


//[RequireComponent(typeof(Rigidbody2D))]
[RequireComponent(typeof(DefenceAttack))]
[RequireComponent(typeof(Collider2D))]
public class DefenceBase : MonoBehaviour
{
    [Header("Defence Stats")]
    [SerializeField] float     maxHealth;
    [SerializeField] HealthBar healthBar;

    [Header("defenceAI")]
    [SerializeField] AttackTriggerType attackTriggerType;
    [SerializeField] DefenceAttack     defenceAttack;
    public Vector2Int myCell;
    public int runDefenceKind;
    public float MaxHealth => maxHealth;


    [Header("Visual settings")]
    [SerializeField] Animator animator;

    float health;
    CustomGridSystem grid;

    private void Awake()
    {
        defenceAttack = GetComponent<DefenceAttack>();
        grid = FindAnyObjectByType<CustomGridSystem>();
    }
    // Start is called once before the first execution of Update after the MonoBehaviour is created
    void Start()
    {
        health = maxHealth;
    }


    // Update is called once per frame
    void Update()
    {
        CheckCurrentHealth();
        if (health <= 0) return;
        //debug code
        EnemyDetectionAndSetAttack();
        EnemyDetectionAndSetAttackMultiLane();
        EnemyDetectionAndSetScared();
    }

    void EnemyDetectionAndSetAttackMultiLane()
    {
        if (AttackTriggerType.OnSightMultiLane != attackTriggerType) { return; }
        for (int i = 0; i <= grid.GetMaxCellCount().y; i++)
        {
            EnemyAI target = grid.GetEnemyIfSomeoneInSight(i);
            if (target!=null)
            {
                defenceAttack.SetTriggerMultiLaneAttack(true,target);
                return;
            }
        }
        defenceAttack.SetTriggerMultiLaneAttack(false,null);
    }

    void EnemyDetectionAndSetAttack()
    {
        if(AttackTriggerType.OnSight != attackTriggerType ) { return; }
        if (grid.isSomeoneInSight(myCell.y))
        {
            defenceAttack.SetTriggerAttack(true);
        }
        else
        {
            defenceAttack.SetTriggerAttack(false);

        }
    }
    void EnemyDetectionAndSetScared()
    {
        if(AttackTriggerType.Static != attackTriggerType) { return; }
        if (grid.isSomeoneInSight(myCell.y))
        {
            defenceAttack.SetTriggerScared(true);
        }
        else
        {
            defenceAttack.SetTriggerScared(false);

        }
    }

    void CheckCurrentHealth()
    {
        if (health <= 0)
        {
            LoggingSystem.LogDefenceDestroyed(gameObject.GetInstanceID().ToString());
            animator.SetBool("isDead", true);
        }
    }

    public void TakeDamage(float damage)
    {
        if (health <= 0) return;
        health -= damage;
        healthBar.UpdateHealth(health, maxHealth);
        if(attackTriggerType == AttackTriggerType.OnPoked)
        {
            //Debug.Log("i got Poked");
            defenceAttack.SetTriggerAttack(true);
        }
    }


    public float GetCurrentHealth()
    {
        return health;
    }

}
