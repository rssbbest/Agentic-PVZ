using UnityEngine;

public class LosingTrigger : MonoBehaviour
{

    [SerializeField] LayerMask enemiesLayer;
    [SerializeField] GameManager gameManager;
    private void Awake()
    {
        gameManager = FindFirstObjectByType<GameManager>();
    }
    private void OnTriggerEnter2D(Collider2D collision)
    {

        if ((enemiesLayer.value & (1 << collision.gameObject.layer)) != 0)
        {
            gameManager.TriggerSimulationOverPanel(false); // UPDATED: enemy reached the end = loss
        }
    }
}