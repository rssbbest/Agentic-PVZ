using UnityEngine;

public class GameBoundManager : MonoBehaviour
{
    private void OnTriggerExit2D(Collider2D collision)
    {
        //Debug.Log($"{collision.gameObject.name} left the simulation area");
        Destroy( collision.gameObject );
    }
}
