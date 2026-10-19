using UnityEngine;
using System.Collections;
using System.Collections.Generic;
using TMPro;
using UnityEngine.SceneManagement;

public class GameManager : MonoBehaviour
{
    [Header("UI")]
    [SerializeField] GameObject simulationOverPanel;
    [SerializeField] float fastForwardTimeScale = 1.5f;
    [SerializeField] float sceneTransitionTime = 5f;


    [Header("Interaction Engine")]
    [SerializeField] float timeToSlowDown = 0.05f;
    [SerializeField] float slowTimeScale = 0.25f;
    [SerializeField] Animator interactionCharacter;
    [SerializeField] List<AnimationClip> animations= new List<AnimationClip>();
    [SerializeField] TextMeshProUGUI interactionText;
    

    [Header("Working Scripts")]
    [SerializeField]VHSEffect vhs;
    [SerializeField] CRTPauseController crt;
    [SerializeField] PlacementSystem defencePlacementSystem;
    [SerializeField] EnemySpawningHandler enemySpawner;

    [Header("DEBUG")]
    [SerializeField] bool quickTestMode = true;

    bool tauntRowTriggered = false;
    bool tauntDefenceTriggered = false;
    bool endScreenTriggered = false;

    List<string> insultRowList = new List<string>
                                {
                                    "You actually thought that would work?",
                                    "Even the zombies are disappointed.",
                                    "That was impressively useless.",
                                    "You really looked at that and said “yes”.",
                                    "You made losing look effortless.",
                                    "I expected nothing and you delivered less."
                                };
    List<string> insultDefenceList = new List<string>
                                {
                                    "You're optimizing for eventual disappointment",
                                    "You make everyone in room appear smarter",
                                    "Your defenses reject the concept of survival.",
                                    "You had ONE job and picked this.",
                                    "You made losing look effortless.",
                                    "I expected nothing and you delivered less."
                                };


    void Start()
    {
        if (AutopilotBridge.Managed) return;
        LoggingSystem.Reset();
        if (quickTestMode)
        {
            enemySpawner.InitializeEnemies("{}", false);
            enemySpawner.StartSpawning();

            defencePlacementSystem.InitializeDefences();
        }
        else
        {
            enemySpawner.InitializeEnemies(NetworkManager.Instance.EnemiesJson, true);
            enemySpawner.StartSpawning();

            defencePlacementSystem.ConvertDefencePlacementJsonToObject(NetworkManager.Instance.DefenceJson);
            defencePlacementSystem.InitializeDefences();
        }
        

    }

    void Update()
    {
        if (AutopilotBridge.Managed) return;
        InteractionEngineConditions();
        SimulationOverPanelConditions();
    }

    private void SimulationOverPanelConditions()
    {
        if (enemySpawner.IsWaveCompleted() && !endScreenTriggered)
        {
            TriggerSimulationOverPanel();

        }
    }


    //Interaction Engine 


    private void InteractionEngineConditions()
    {

        if (defencePlacementSystem.isARowClearOfDefences() && !tauntRowTriggered)
        {
            FastForwardUp();
            tauntRowTriggered = true;
            string taunt = insultRowList[Random.Range(0, insultRowList.Count)];
            StartCoroutine(StartInteractionEngine(taunt));
        }

        bool condition = (defencePlacementSystem.DefenceCount() <= defencePlacementSystem.MaxDefenceCount()/2 ) && (enemySpawner.TotalEnemiesInWave() / 2 <= enemySpawner.EnemyCountInSpawnQueue());


        if (condition && !tauntDefenceTriggered)
        {
            FastForwardUp();
            tauntDefenceTriggered = true;
            string taunt = insultDefenceList[Random.Range(0, insultDefenceList.Count)];
            StartCoroutine(StartInteractionEngine(taunt));
        }
        
    }

    IEnumerator StartInteractionEngine(string taunt)
    {
        crt.ActivateEffect();
        interactionText.text = taunt;
        //start slow down effect
        for (int i = 1; Time.timeScale>slowTimeScale;i++)
        {
            Time.timeScale = 1 - i*0.1f;
            //Debug.Log(Time.timeScale);
            yield return new WaitForSecondsRealtime(timeToSlowDown);
        }
        interactionCharacter.gameObject.SetActive(true);
        interactionCharacter.Play(animations[Random.Range(0,animations.Count)].name,0,0f);
        yield return null;
    }

    IEnumerator DeactivateInteractionEngine()
    {
        crt.DeactivateEffect();
        //start slow down effect
        for (int i = 1; Time.timeScale < 1f; i++)
        {
            Time.timeScale += 0.1f;
            //Debug.Log(Time.timeScale);
            yield return new WaitForSecondsRealtime(timeToSlowDown);
        }
        yield return null;
    }



    //PUBLIC FUNCS

    // end game panel
    public void TriggerSimulationOverPanel()
    {
        if (AutopilotBridge.Managed || endScreenTriggered) return;
        endScreenTriggered = true;
        StartCoroutine(SimulationOverCleanUp());
    }

    public void TurnOffInteractionEngine()
    {
        StartCoroutine(DeactivateInteractionEngine());
    }


    IEnumerator SimulationOverCleanUp()
    {
        bool successValue = false;
        string messageValue = "";

        if (!quickTestMode)
        {
            string result = LoggingSystem.Compile(FindObjectsByType<EnemyAI>(FindObjectsSortMode.None).Length == 0, NetworkManager.Instance.teamID, true);
            NetworkManager.Instance.PushJson(result, (success, message) =>
            {
                successValue = success;
                messageValue = message;
            });
        }
        

        Time.timeScale = 0f;
        interactionCharacter.StopPlayback();
        crt.ActivateEffect();
        simulationOverPanel.SetActive(true);

        yield return new WaitForSecondsRealtime(sceneTransitionTime);

        if (!quickTestMode)
        {
            if (successValue)
            {
                SceneManager.LoadScene(0);
            }
        }
        

    }
    //Fast Forward
    public void FastForwardDown()
    {
        Time.timeScale = fastForwardTimeScale;
        vhs.Activate();
    }
    public void FastForwardUp()
    {
        Time.timeScale = 1f;
        vhs.Deactivate();
    }
}
