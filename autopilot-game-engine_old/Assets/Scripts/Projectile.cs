using System;
using UnityEngine;

public enum ProjectileType
{
    enemy,
    defence
}
public enum ProjectilePathType
{
    straight,
    arial
}


[RequireComponent(typeof(Rigidbody2D))]
[RequireComponent(typeof(Collider2D))]
public class Projectile : MonoBehaviour
{

    [Header("Collision settings")]
    [SerializeField] LayerMask      collisionLayer;
    [SerializeField] ProjectileType projectileType;

    [Header("Projectile behaviour settings")]
    [SerializeField] ProjectilePathType projectilePathType;

    [Header("Visuals Settings")]
    [SerializeField] ParticleSystem hitEffect;

    // common
    float   speed;
    float   damage;
    DefenceBase defB;
    EnemyAI enemyB;
    string ownerId;
    Vector2Int ownerCell;
    bool hitOnce;

    // straight
    Vector3 direction;


    // arial
    [SerializeField] EnemyAI target;
    float arcHeight = 3f;
    bool active;



    //runtime req
    Vector3 startPosition;
    Vector3 lastKnownTargetPos;
    float step = 0f;
    float initialDistance;




    private void OnTriggerEnter2D(Collider2D collision)
    {
        if (hitOnce) return;
        if ((collisionLayer.value & (1 << collision.gameObject.layer)) != 0)
        {
            Vector2 contactPosition = collision.ClosestPoint(new Vector2(transform.position.x, transform.position.y));
            if (projectileType == ProjectileType.enemy)
            {
                DefenceBase defenceBase = collision.GetComponent<DefenceBase>();
                if (defenceBase == null) return;
                hitOnce = true;
                defenceBase.TakeDamage(damage);

                //logging
                if (!string.IsNullOrEmpty(ownerId)) LoggingSystem.LogEnemyDamage(ownerId, damage);

                PlayHitEffect(contactPosition);
                Destroy(gameObject);
            }
            else if (projectileType == ProjectileType.defence)
            {
                EnemyAI enemyBase = collision.GetComponent<EnemyAI>();
                if (enemyBase == null) return;
                hitOnce = true;
                DamageVisualizer damageVisualizer = collision.GetComponent<DamageVisualizer>();
                if (damageVisualizer != null) damageVisualizer.TriggerDamageVisuals(damage,contactPosition);
                enemyBase.TakeDamage(damage);

                //logging
                if (!string.IsNullOrEmpty(ownerId)) LoggingSystem.LogDefenceDamage(ownerId, ownerCell, damage);

                PlayHitEffect(contactPosition);
                Destroy(gameObject);
            }

            


        }
    }

    void PlayHitEffect(Vector3 position)
    {
        if (hitEffect != null)
        {
            Instantiate(hitEffect, position, Quaternion.identity);
        }
    }
    void PlayHitEffect(Vector2 position)
    {
        if (hitEffect != null)
        {
            Instantiate(hitEffect, new Vector3(position.x,position.y,0), Quaternion.identity);
        }
    }


    void Update()
    {
        switch (projectilePathType)
        {
            case ProjectilePathType.straight:
                StraightTypeMovement();
                break;

            case ProjectilePathType.arial:
                ArialTypeMovement();
                break;

        }
            
    }

    void ArialTypeMovement()
    {
        if (!active) { return; }
        // 1. Handle Target Death Mid-Flight
        if (target != null)
        {
            lastKnownTargetPos = target.transform.position;
        }

        // 2. Advance the interpolation time (t)
        // Dividing speed by initialDistance ensures it doesn't move absurdly fast at close range
        step += (speed / initialDistance) * Time.deltaTime;
        float t = Mathf.Clamp01(step);

        // 3. Define the three points for the Bezier Curve
        Vector3 p0 = startPosition;
        Vector3 p2 = lastKnownTargetPos; // Uses live position if alive, or last known if dead

        // Control point is halfway between p0 and p2, but shifted UP by arcHeight
        Vector3 p1 = p0 + (p2 - p0) / 2f + Vector3.up * arcHeight;

        // 4. Calculate the current position on the curve using the Bezier formula
        // Formula: B(t) = (1-t)^2 * P0 + 2(1-t)t * P1 + t^2 * P2
        Vector3 nextPosition = Mathf.Pow(1 - t, 2) * p0 +
                               2 * (1 - t) * t * p1 +
                               Mathf.Pow(t, 2) * p2;

        // 5. Rotate the projectile to face its movement direction (like an arrow)
        Vector3 direction = nextPosition - transform.position;
        if (direction != Vector3.zero)
        {
            // Adjust Atan2 depending on your sprite's default orientation
            float angle = Mathf.Atan2(direction.y, direction.x) * Mathf.Rad2Deg;
            transform.rotation = Quaternion.Euler(0, 0, angle);
        }

        // 6. Move the projectile
        transform.position = nextPosition;

        // 7. Check for impact
        if (t >= 1f)
        {
            StartDecaySequence();
        }
    }




    public void SetAttributes(float speedG, float damageG, Vector3 directionG,DefenceBase b,EnemyAI a)
    {
        active = false;
        damage = damageG;
        speed = speedG;
        direction = directionG;
        defB = b;
        enemyB = a;
        hitOnce = false;
        ownerId = b != null ? b.gameObject.GetInstanceID().ToString() :
                  a != null ? a.gameObject.GetInstanceID().ToString() : null;
        ownerCell = b != null ? b.myCell : Vector2Int.zero;
    }
    public void SetAttributes(float speedG, float damageG,float arcHeightG, EnemyAI targetG, DefenceBase b)
    {
        active = false;
        damage = damageG;
        speed = speedG;
        arcHeight = arcHeightG;
        target = targetG;
        defB = b;
        hitOnce = false;
        ownerId = b != null ? b.gameObject.GetInstanceID().ToString() : null;
        ownerCell = b != null ? b.myCell : Vector2Int.zero;

        startPosition = transform.position;

        if (target != null)
        {
            initialDistance = Vector3.Distance(startPosition, target.transform.position);
            lastKnownTargetPos = target.transform.position;
        }
    }

    void StartDecaySequence()
    {
        Destroy(gameObject);
    }

    void StraightTypeMovement()
    {
        if (active)
        {
            transform.position += direction.normalized * speed * Time.deltaTime;
        }
    }

    public void Throw()
    {
        active = true;
    }
}
