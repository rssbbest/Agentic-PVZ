using System;
using System.Runtime.CompilerServices;
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
    [Header("Currecy System")]
    [SerializeField] int Cost = 100;

    [Header("Defence Stats")]
    [SerializeField] float     maxHealth;
    [SerializeField] HealthBar healthBar;

    [Header("defenceAI")]
    [SerializeField] AttackTriggerType attackTriggerType;
    [SerializeField] DefenceAttack     defenceAttack;
    public Vector2Int myCell;


    [Header("Visual settings")]
    [SerializeField] Animator animator;

    float health;
    bool isDead = false; // NEW: destruction logged once
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
        if (grid.isSomeoneInSight(myCell.y, myCell.x)) // UPDATED: skip enemies that already passed me
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
        if (grid.isSomeoneInSight(myCell.y, myCell.x)) // UPDATED: skip enemies that already passed me
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
        // UPDATED: log and start the death animation once, at the moment of death
        if (health <= 0 && !isDead)
        {
            isDead = true;
            LoggingSystem.LogDefenceDestroyed(transform.name, myCell);
            animator.SetBool("isDead", true);
        }
    }

    public void TakeDamage(float damage)
    {

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

    // NEW: max health for state snapshot
    public float GetMaxHealth()
    {
        return maxHealth;
    }

}
