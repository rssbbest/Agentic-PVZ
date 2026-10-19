using UnityEngine;

public class EventParentConnector : MonoBehaviour
{
    [Header("Melee settings")]
    [SerializeField] ParticleSystem meleeEffect;

    EnemyAI     enemy;
    EnemyAttack enemyAttack;



    private void Awake()
    {
        enemy= transform.parent.GetComponent<EnemyAI>();
        enemyAttack= transform.parent.GetComponent<EnemyAttack>();
    }
    public void EnemyMeeleAnimationEvent()
    {
        meleeEffect.Play();
        enemy.EnemyAttackEvent();
    }
    public void EnemyLazerAnimationEvent()
    {
        //Debug.Log("laser triggered");
        enemyAttack.SetLaserON();
    }

    public void EnemyThrowAnimationEvent()
    {
        enemyAttack.ThrowProjectile();
    }

    public void DisableGameobject()
    {
        transform.parent.gameObject.SetActive(false);
    }
}
