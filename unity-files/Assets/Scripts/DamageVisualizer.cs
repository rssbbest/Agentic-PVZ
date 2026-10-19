using UnityEngine;
using System.Collections.Generic;
using System.Collections;


public class DamageVisualizer : MonoBehaviour
{
    [Header("Visual settings")]
    [SerializeField] Gradient colorGradient;
    [SerializeField] bool isKnockbackEnabled;
    [SerializeField] float knockbackForce;
    [SerializeField] float reactionTime;

    [Header("requirements")]
    [SerializeField] List<SpriteRenderer> spriteRendererList = new List<SpriteRenderer>();
    [SerializeField] DamageVisualizerTextHandler textHandler;

    EnemyAI enemyAI;

    private void Awake()
    {
        enemyAI = GetComponent<EnemyAI>();
        //textHandler = GetComponentInChildren<DamageVisualizerTextHandler>();
    }

    public void TriggerDamageVisuals(float damage,Vector3 pos)
    {
        //textHandler.SpawnText(damage,pos);
        StartCoroutine(DamageVisualSequence());
    }

    IEnumerator DamageVisualSequence()
    {
        float timeCurrent = 0f;
        float timeNormalized = 0f;

        if (isKnockbackEnabled && !enemyAI.IsDead())
        {
            transform.position += (enemyAI.directionOfAttack * -1).normalized * knockbackForce;
        }

        for (; timeNormalized<= 1;)
        {
            timeCurrent+= Time.deltaTime;
            timeNormalized = timeCurrent / reactionTime;
            Color color =  colorGradient.Evaluate(timeNormalized);
            ApplyColor(color);
            yield return new WaitForSecondsRealtime(Time.deltaTime);
        }
        ApplyColor(Color.white);
        yield return null;
    }


    void ApplyColor(Color color)
    {
        foreach (var renderer in spriteRendererList)
        {
            renderer.color = color;
        }
    }

}
