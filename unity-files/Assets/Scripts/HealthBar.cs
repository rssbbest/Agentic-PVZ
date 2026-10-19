using UnityEngine;


public class HealthBar : MonoBehaviour
{
    [Header("visual Settings")]
    [SerializeField] Gradient       colorGradient;
    [SerializeField] SpriteRenderer spriteRenderer;


    private float originalWidth;

    private void Awake()
    {
        originalWidth = spriteRenderer.transform.localScale.x;
    }

    public void UpdateHealth(float currentHealth, float maxHealth)
    {
        float healthPercent = Mathf.Clamp01(currentHealth / maxHealth);

        // Color
        spriteRenderer.color = colorGradient.Evaluate(healthPercent);

        // Width
        Vector3 scale = spriteRenderer.transform.localScale;
        scale.x = originalWidth * healthPercent;
        spriteRenderer.transform.localScale = scale;
    }

}
