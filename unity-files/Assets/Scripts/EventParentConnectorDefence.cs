using UnityEngine;

public class EventParentConnectorDefence : MonoBehaviour
{
    DefenceAttack defenceAttack;

    private void Awake()
    {
        defenceAttack = transform.parent.GetComponent<DefenceAttack>();
    }


    public void DefenceThrowAnimationEvent()
    {
        defenceAttack.ThrowerLaneProjectile();
    }
    public void DefenceThrowMultiLaneAnimationEvent()
    {
        defenceAttack.ThrowerMultiLaneProjectile();
    }

    public void DestroyParentGameObject()
    {
        Destroy(transform.parent.gameObject);
    }
}
