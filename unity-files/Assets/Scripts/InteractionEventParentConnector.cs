using UnityEngine;
using UnityEngine.Events;

public class InteractionEventParentConnector : MonoBehaviour
{
    [Header("Events")]
    public UnityEvent DeactivateInteractionEngine;

    public void DeactivateInteractionEngineEvent()
    {
        DeactivateInteractionEngine?.Invoke();
    }

}
