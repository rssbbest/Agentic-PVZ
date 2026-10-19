using UnityEngine;


public class LosingTrigger : MonoBehaviour
{

    [SerializeField] LayerMask enemiesLayer;
    [SerializeField] GameManager gameManager;
    private void Awake()
    {
        gameManager = FindAnyObjectByType<GameManager>();
    }
    private void OnTriggerEnter2D(Collider2D collision)
    {

        if ((enemiesLayer.value & (1 << collision.gameObject.layer)) != 0)
        {
            if (AutopilotBridge.Managed) Destroy(collision.gameObject);
            else gameManager.TriggerSimulationOverPanel();
        }
    }
}
