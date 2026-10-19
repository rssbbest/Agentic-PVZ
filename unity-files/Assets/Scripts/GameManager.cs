using UnityEngine;
using System.Collections;
using System.Collections.Generic;
using TMPro;
using UnityEngine.SceneManagement;

public class GameManager : MonoBehaviour
{

    public static GameManager Instance { get; set; }

    [Header("UI")]
    [SerializeField] GameObject simulationOverPanel;
    [SerializeField] GameObject lossScreen;
    [SerializeField] GameObject winScreen;
    [SerializeField] float fastForwardTimeScale = 1.5f;
    [SerializeField] float sceneTransitionTime = 5f; // UPDATED: realtime seconds on the end screen before results are pushed
    [SerializeField] float resultsPushMaxWait = 40f;  // NEW: realtime cap on the results push, then the menu loads anyway


    [Header("Interaction Engine")]
    [SerializeField] float timeToSlowDown = 0.05f;
    [SerializeField] float slowTimeScale = 0.25f;
    [SerializeField] Animator interactionCharacter;
    [SerializeField] List<AnimationClip> animations= new List<AnimationClip>();
    [SerializeField] TextMeshProUGUI interactionText;
    [SerializeField] int closeCallColumn = 1; // NEW: close-call taunt when a living enemy reaches this column or lower (0 = next to base)
    [SerializeField] float closeCallSeconds = 4f; // NEW: close-call taunt when an unblocked enemy is this many game seconds from the base
    [SerializeField] float badPlayMargin = 0.1f;  // NEW: bad-play taunt when % of defences lost beats % of wave killed by this much
    

    [Header("Working Scripts")]
    [SerializeField]VHSEffect vhs;
    [SerializeField] CRTPauseController crt;
    [SerializeField] PlacementSystem defencePlacementSystem;
    [SerializeField] EnemySpawningHandler enemySpawner;

    // NEW: mid-game state capture (screenshot / JSON snapshot)
    [Header("State Capture")]
    [SerializeField] bool captureEnabled = true;
    [SerializeField][Range(1, 100)] int screenshotJpgQuality = 75;
    [SerializeField] int screenshotMaxWidth = 1280; // 0 = no downscale
    [SerializeField] bool logStateSnapshotLocally = true;

    // NEW: mid-run reinforcement ping (backend may place extra defences)
    [Header("Reinforcements")]
    [SerializeField] bool reinforcementsEnabled = true;          // always off in quickTestMode
    [SerializeField] float reinforcementIntervalSeconds = 30f;   // UPDATED: game seconds between pauses (fast-forward speeds it up), <= 0 = off
    [SerializeField] float reinforcementHoldSeconds = 1f;        // UPDATED: realtime hold after the decision text is shown
    [SerializeField] bool sendScreenshotToo = false;             // clean screenshot per ping, best effort

    // NEW: CRT text per reinforcement state
    [Header("Reinforcement Messages")]
    [SerializeField] string reinforcementWaitingText = "Waiting for agent response";
    [SerializeField] string reinforcementPlaceText = "Agent chose to place defences";
    [SerializeField] string reinforcementSkipText = "Agent chose to skip";
    [SerializeField] string reinforcementFailedText = "No response from agent, resuming";
    [SerializeField] Color reinforcementWaitingColor = new Color(0.45f, 0.85f, 1f);  // cyan
    [SerializeField] Color reinforcementPlaceColor = new Color(0.4f, 1f, 0.55f);     // green
    [SerializeField] Color reinforcementSkipColor = new Color(1f, 0.8f, 0.3f);       // amber
    [SerializeField] Color reinforcementFailedColor = new Color(1f, 0.35f, 0.35f);   // red

    // NEW: coin balance sent by backend (team data + every reinforcement response)
    [Header("Coins")]
    [SerializeField] TextMeshProUGUI coinText;
    [SerializeField] string coinFormat = "Coins: {0}";
    [SerializeField] float coinCountTime = 0.6f;
    [SerializeField] float coinPopScale = 1.2f;
    [SerializeField] Color coinGainColor = new Color(0.4f, 1f, 0.55f);  // green
    [SerializeField] Color coinLossColor = new Color(1f, 0.35f, 0.35f); // red

    [Header("DEBUG")]
    [SerializeField] bool quickTestMode = true;

    bool tauntRowTriggered = false;
    bool tauntDefenceTriggered = false;
    bool tauntCloseCallTriggered = false; // NEW: close-call taunt fires once
    bool endScreenTriggered = false;
    bool captureInProgress = false; // NEW: one screenshot at a time
    GamePhase phase = GamePhase.Running; // NEW: who owns timeScale / CRT
    ReinforcementScheduler reinforcementScheduler; // NEW: null = feature off
    float initTimeScaleBeforeTaunt = 0f;
    // NEW: coin display state
    int coins = 0;
    Coroutine coinAnim;
    Vector3 coinBaseScale = Vector3.one;
    Color coinBaseColor = Color.white;


    List<string> insultRowList = new List<string>
                                {
                                    "When failure saw you it felt successful",
                                    "Let me guess, you rotate the square in tetris",
                                    "As an outsider , what are your views on intelligence",
                                    "You really looked at that and said “yes”.",
                                    "I know you tried your best. That's what makes it so dissapointing",
                                    "Its hard to underestimate you"
                                };
    List<string> insultDefenceList = new List<string>
                                {
                                    "Act like your hairline and back up!",
                                    "That looked like a left handed drawing",
                                    "I envy the people that don't know you",
                                    "You lighten every room u walk out of",
                                    "You made losing look effortless.",
                                    "I expected nothing and you delivered less."
                                };

    private void Awake()
    {
        Instance = this;

        // NEW: remember the coin text's scene look
        if (coinText != null)
        {
            coinBaseScale = coinText.rectTransform.localScale;
            coinBaseColor = coinText.color;
        }
    }

    void Start()
    {
        SimClock.Fix(); // NEW: fixed game step per frame, so every machine plays a level the same way
        LoggingSystem.Reset(); // NEW: fresh logs + startTime each simulation
        if (quickTestMode)
        {
            enemySpawner.InitializeEnemies("{}", false);
            enemySpawner.StartSpawning();

            defencePlacementSystem.InitializeDefences();
        }
        else
        {
            enemySpawner.InitializeEnemies(NetworkManager.Instance.EnemiesJson, true);
            enemySpawner.ApplySpawnSettings(NetworkManager.Instance.SpawnSettingsJson); // NEW: backend spawn timing
            enemySpawner.StartSpawning();

            defencePlacementSystem.ConvertDefencePlacementJsonToObject(NetworkManager.Instance.DefenceJson);
            defencePlacementSystem.InitializeDefences();

            ApplyGameSettings(NetworkManager.Instance.GameSettingsJson); // NEW: backend reinforcement settings

            // NEW: interval starts after initial placement, first ping one interval later
            bool reinforcementsOn = reinforcementsEnabled && NetworkManager.Instance != null;
            reinforcementScheduler = new ReinforcementScheduler(reinforcementIntervalSeconds, reinforcementsOn, Time.time); // UPDATED: game time
        }

        // NEW: starting balance counts up from 0 (0 without backend)
        int startCoins = 0;
        if (!quickTestMode)
        {
            startCoins = NetworkManager.Instance.Coins;
        }
        UpdateCoins(startCoins);
        

    }

    void Update()
    {
        
        SimulationOverPanelConditions();
        ReinforcementConditions(); // NEW: after game-over check so a finished wave wins
        InteractionEngineConditions();

        //if (quickTestMode)
        //{
        //    return;
        //}

        //test 
        //if (Input.GetKeyDown(KeyCode.P))
        //{
        //    CaptureAndSendGameState();
        //}
    }

    void SetTimeScale(float scale,string msg)
    {
        Time.timeScale = scale;
        //Debug.Log($"SET due to : {msg} . TimeScale now is {scale}");
    }

    private void SimulationOverPanelConditions()
    {
        if (enemySpawner.IsWaveCompleted() && !endScreenTriggered)
        {
            TriggerSimulationOverPanel(true); // UPDATED: all enemies killed = win
        }
    }


    //Interaction Engine 


    private void InteractionEngineConditions()
    {
        // UPDATED: one taunt at a time, never during reinforcements or after game over
        if (phase != GamePhase.Running || !crt.IsFullyIdle())
        {
            return;
        }

        //bool condition1 = true;

        //if (condition1 && !tauntRowTriggered)
        //{
        //    FastForwardUp();
        //    tauntRowTriggered = true;
        //    SetPhase(GamePhase.InteractionEngine); // NEW
        //    string taunt = insultRowList[Random.Range(0, insultRowList.Count)];
        //    StartCoroutine(StartInteractionEngine(taunt));
        //}

        // UPDATED: close call, an unblocked enemy is about to breach, or one is already at the door
        if (!tauntCloseCallTriggered && IsCloseCall())
        {
            FastForwardUp();
            tauntCloseCallTriggered = true;
            SetPhase(GamePhase.InteractionEngine);
            string taunt = insultRowList[Random.Range(0, insultRowList.Count)];
            tauntRoutine = StartCoroutine(StartInteractionEngine(taunt));
            return; // only one taunt this frame
        }

        //bool condition2 = (defencePlacementSystem.DefenceCount() <= defencePlacementSystem.MaxDefenceCount()/3 );
        bool condition2 = !tauntDefenceTriggered && IsPlayingBadly(); // UPDATED: losing defences faster than clearing the wave


        if (condition2 && !tauntDefenceTriggered)
        {
            FastForwardUp();
            tauntDefenceTriggered = true;
            SetPhase(GamePhase.InteractionEngine); // NEW
            string taunt = insultDefenceList[Random.Range(0, insultDefenceList.Count)];
            tauntRoutine = StartCoroutine(StartInteractionEngine(taunt));
        }
        
    }

    // NEW: an unblocked enemy is about to reach the base, or one is already fighting at the door
    bool IsCloseCall()
    {
        if (enemySpawner.IsBreakthroughImminent(closeCallSeconds, defencePlacementSystem))
        {
            return true;
        }
        return enemySpawner.IsEnemyNearBase(closeCallColumn);
    }

    // NEW: at least one defence lost, and the share of defences lost is ahead of the share of the wave killed
    bool IsPlayingBadly()
    {
        int placed = defencePlacementSystem.MaxDefenceCount();
        int lost = placed - defencePlacementSystem.DefenceCount();
        int totalEnemies = enemySpawner.TotalEnemiesInWave();
        if (placed <= 0 || lost < 1 || totalEnemies <= 0)
        {
            return false;
        }

        // killed = spawned and already gone (queued and on-field enemies are still alive)
        int killed = totalEnemies - enemySpawner.EnemyCountInSpawnQueue() - enemySpawner.ActiveEnemyCount();
        float lostShare = (float)lost / placed;
        float killedShare = (float)killed / totalEnemies;
        return lostShare - killedShare >= badPlayMargin;
    }

    // NEW: wait `seconds` worth of fixed frames (game time keeps moving, so real time must not decide how much)
    static IEnumerator WaitFrames(float seconds)
    {
        for (int i = SimClock.Frames(seconds); i > 0; i--)
        {
            yield return null;
        }
    }

    IEnumerator StartInteractionEngine(string taunt)
    {
        initTimeScaleBeforeTaunt = Time.timeScale;
        crt.ActivateEffect();
        interactionText.text = taunt;
        //start slow down effect
        for (int i = 1; Time.timeScale>slowTimeScale;i++)
        {
            SetTimeScale(1 - i*0.1f,$"starting interaction engine ");
            //Debug.Log(Time.timeScale);
            yield return WaitFrames(timeToSlowDown); // UPDATED: frames, not real time (repeatable)
        }
        // NEW: game ended during the slow-down, don't pop the character over the end screen
        if (phase == GamePhase.Over)
        {
            yield break;
        }
        interactionCharacter.gameObject.SetActive(true);
        interactionCharacter.Play(animations[Random.Range(0,animations.Count)].name,0,0f);
        yield return null;
    }

    IEnumerator DeactivateInteractionEngine()
    {
        // NEW: game ended during the taunt, leave the end screen's CRT and time scale alone
        // UPDATED: a reinforcement pause cut the taunt short: the pause owns time and CRT now
        if (phase == GamePhase.Over || phase == GamePhase.ReinforcementPause)
        {
            yield break;
        }
        crt.DeactivateEffect();
        //start slow down effect
        for (int i = 1; Time.timeScale < 1f; i++)
        {
            SetTimeScale(Time.timeScale + 0.1f,"deactivating interaction Engine ");
            //Debug.Log(Time.timeScale);
            yield return WaitFrames(timeToSlowDown); // UPDATED: frames, not real time (repeatable)
        }
        SetTimeScale(initTimeScaleBeforeTaunt, "defaulting to initialTimeScale before interaction engine started");
        
        // NEW: taunt finished, game is free again
        if (phase == GamePhase.InteractionEngine)
        {
            SetPhase(GamePhase.Running);
        }
        yield return null;
    }


    //Reinforcements

    // NEW: overlay backend "gameSettings" onto inspector values (missing keys keep inspector values)
    void ApplyGameSettings(string levelJson)
    {
        GameSettings defaults = new GameSettings();
        defaults.reinforcementsEnabled = reinforcementsEnabled;
        defaults.reinforcementIntervalSeconds = reinforcementIntervalSeconds;
        defaults.reinforcementHoldSeconds = reinforcementHoldSeconds;
        defaults.sendScreenshotToo = sendScreenshotToo;

        GameSettings settings = GameSettingsRules.ParseGameSettings(levelJson, defaults);
        reinforcementsEnabled = settings.reinforcementsEnabled;
        reinforcementIntervalSeconds = settings.reinforcementIntervalSeconds;
        reinforcementHoldSeconds = settings.reinforcementHoldSeconds;
        sendScreenshotToo = settings.sendScreenshotToo;

        Debug.Log(GameSettingsRules.LogPrefix + "enabled=" + reinforcementsEnabled + " interval=" + reinforcementIntervalSeconds
                  + " hold=" + reinforcementHoldSeconds + " screenshot=" + sendScreenshotToo);
    }

    // NEW: single writer for phase, Over is final
    void SetPhase(GamePhase requested)
    {
        phase = ReinforcementRules.NextPhase(phase, requested);
    }

    // NEW: start a reinforcement pause when due and the game is free
    void ReinforcementConditions()
    {
        if (reinforcementScheduler == null)
        {
            return;
        }
        // UPDATED: game time, so a taunt's slow motion stretches the wait with the game
        bool due = reinforcementScheduler.Tick(Time.time, phase, enemySpawner.IsWaveCompleted(),
                                               endScreenTriggered, crt.IsFullyIdle());
        if (!due)
        {
            return;
        }
        if (phase == GamePhase.InteractionEngine)
        {
            CutTaunt(); // NEW: the pause takes over from the slowed game
        }
        SetPhase(GamePhase.ReinforcementPause);
        StartCoroutine(ReinforcementPauseRoutine());
    }

    Coroutine tauntRoutine; // NEW: the running taunt, so a due pause can cut it short

    // NEW: end the taunt now, keeping the CRT on: the reinforcement pause continues from
    // the current (slowed) time scale and restores normal speed when it ends.
    void CutTaunt()
    {
        if (tauntRoutine != null)
        {
            StopCoroutine(tauntRoutine);
            tauntRoutine = null;
        }
        interactionCharacter.gameObject.SetActive(false);
        interactionText.text = "";
    }

    bool openingRequested;

    // Called synchronously by the spawner after activation, before its next yield.
    // This opening is mandatory even when subsequent reinforcements are disabled.
    public IEnumerator OpeningAfterFirstSpawn()
    {
        if (openingRequested || quickTestMode || NetworkManager.Instance == null || phase == GamePhase.Over)
            return null;
        openingRequested = true;
        SetPhase(GamePhase.ReinforcementPause);
        SetTimeScale(0f, "first enemy spawned: opening decision");
        return ReinforcementPauseRoutine();
    }

    public void CallReinforcementsFORCEFULLY()
    {
        SetPhase(GamePhase.ReinforcementPause);
        StartCoroutine(ReinforcementPauseRoutine());
    }

    // NEW: one full reinforcement cycle; aborts silently if the game ends (cleanup owns time/CRT)
    IEnumerator ReinforcementPauseRoutine()
    {
        // 1. optional clean screenshot, BEFORE the CRT takes over the game camera
        if (sendScreenshotToo)
        {
            Coroutine shot = BeginScreenshotCapture();
            if (shot != null)
            {
                yield return shot;
            }
            if (phase != GamePhase.ReinforcementPause)
            {
                yield break;
            }
        }

        // 2. CRT zoom-out, timeScale eases to exactly 0 on the same clock and curve
        vhs.Deactivate(); // fast-forward is suspended now and not restored on resume
        crt.ActivateEffect(); // UPDATED: text set below
        crt.SetMessage(reinforcementWaitingText, reinforcementWaitingColor, true); // NEW: typed in, looping dots
        float startScale = Time.timeScale;
        float duration = Mathf.Max(0.01f, crt.duration); // same floor as CRTPauseController.Animate
        float elapsed = 0f;
        while (elapsed < duration)
        {
            elapsed += SimClock.Step; // UPDATED: fixed step, so the ease covers the same game time everywhere
            SetTimeScale(ReinforcementRules.EaseDownScale(elapsed, duration, crt.ease, startScale),"reinforcements slowing");
            yield return null;
            if (phase != GamePhase.ReinforcementPause)
            {
                yield break;
            }
        }
        SetTimeScale(0f, "reinforcement pause");
        

        // 3. snapshot at the freeze instant, then ask the backend
        bool requestDone = false;
        bool requestOk = false;
        string responseText = "";
        string snapshot = LoggingSystem.CaptureStateSnapshot(NetworkManager.Instance.teamID, NetworkManager.Instance.SessionID);
        if (logStateSnapshotLocally)
        {
            Debug.Log(ReinforcementRules.LogPrefix + "request:\n" + snapshot);
        }
        NetworkManager.Instance.PushReinforcementRequest(snapshot, (success, message) =>
        {
            // locals of this routine only; harmless if the routine was aborted
            requestDone = true;
            requestOk = success;
            responseText = message;
        });
        while (!requestDone)
        {
            yield return null;
            if (phase != GamePhase.ReinforcementPause)
            {
                yield break;
            }
        }

        // 4. place or skip (no per-card validation; the catch only keeps the game from staying frozen)
        int placed = 0;
        if (!requestOk)
        {
            Debug.LogWarning(ReinforcementRules.LogPrefix + "request failed, resuming: " + responseText);
            crt.SetMessage(reinforcementFailedText, reinforcementFailedColor, false); // NEW
        }
        else if (ReinforcementRules.ShouldPlace(responseText))
        {
            crt.SetMessage(reinforcementPlaceText, reinforcementPlaceColor, false); // NEW
            try
            {
                placed = defencePlacementSystem.PlaceDefencesMidRun(responseText);
            }
            catch (System.Exception e)
            {
                Debug.LogError(ReinforcementRules.LogPrefix + "placement failed, resuming: " + e.Message);
            }
        }
        else
        {
            Debug.Log(ReinforcementRules.LogPrefix + "backend skipped");
            crt.SetMessage(reinforcementSkipText, reinforcementSkipColor, false); // NEW
        }

        // NEW: balance after this decision, shown once the CRT has zoomed back in
        int newCoins = coins;
        if (requestOk)
        {
            newCoins = ReinforcementRules.ParseCoins(responseText, coins);
        }

        // 5. UPDATED: hold on every outcome so the decision text can be read and new defences show up in the CRT feed
        float held = 0f;
        while (held < reinforcementHoldSeconds)
        {
            yield return null;
            if (phase != GamePhase.ReinforcementPause)
            {
                yield break;
            }
            // NEW: hold starts once the decision text has finished typing
            if (!crt.IsMessageAnimating())
            {
                held += Time.unscaledDeltaTime;
            }
        }

        // 6. CRT zoom-in, timeScale eases to exactly 1 (fast-forward is not restored)
        crt.DeactivateEffect();
        elapsed = 0f;
        while (elapsed < duration)
        {
            elapsed += SimClock.Step; // UPDATED: fixed step, so the ease covers the same game time everywhere
            SetTimeScale(ReinforcementRules.EaseUpScale(elapsed, duration, crt.ease), "reinforcement scale up");

            ;
            yield return null;
            if (phase != GamePhase.ReinforcementPause)
            {
                yield break;
            }
        }
        SetTimeScale(1f, "reinforcement normal");
        UpdateCoins(newCoins); // NEW: after zoom-in so the count is visible

        // 7. free again, interval restarts now
        SetPhase(GamePhase.Running);
        reinforcementScheduler.Restart(Time.time); // UPDATED: game time
    }



    //PUBLIC FUNCS

    public void BackToMainMenu()
    {
        SceneManager.LoadScene(0);
    }

    // end game panel
    public void TriggerSimulationOverPanel(bool isWin) // UPDATED: win/loss flag
    {
        // NEW: end simulation only once, so results are pushed once
        if (endScreenTriggered)
        {
            return;
        }
        endScreenTriggered = true;
        SetPhase(GamePhase.Over); // NEW: end of game wins, aborts a reinforcement pause

        StartCoroutine(SimulationOverCleanUp(isWin));
    }

    public void TurnOffInteractionEngine()
    {
        StartCoroutine(DeactivateInteractionEngine());
    }

    // NEW: capture whole screen as seen in game view and send it to the backend
    public void CaptureAndSendScreenshot()
    {
        BeginScreenshotCapture(); // UPDATED: shared with the reinforcement ping
    }

    // NEW: same guards as before; returns the capture coroutine, or null if skipped
    Coroutine BeginScreenshotCapture()
    {
        if (!captureEnabled)
        {
            return null;
        }
        if (captureInProgress)
        {
            Debug.Log("[Capture] Screenshot already in progress, request ignored");
            return null;
        }
        if (Screen.width <= 0 || Screen.height <= 0)
        {
            Debug.LogWarning("[Capture] Screen has no pixels, screenshot skipped");
            return null;
        }
        captureInProgress = true;
        return StartCoroutine(CaptureScreenshotRoutine());
    }

    // NEW: capture after rendering, encode JPG in memory, release textures, upload
    IEnumerator CaptureScreenshotRoutine()
    {
        // frame must finish rendering first; not affected by Time.timeScale
        yield return new WaitForEndOfFrame();

        Texture2D full = null;
        Texture2D sized = null;
        byte[] jpg = null;
        try
        {
            full = ScreenCapture.CaptureScreenshotAsTexture();
            sized = CaptureImaging.Downscale(full, screenshotMaxWidth);
            if (sized != null)
            {
                jpg = sized.EncodeToJPG(screenshotJpgQuality);
            }
        }
        catch (System.Exception e)
        {
            Debug.LogWarning("[Capture] Screenshot capture failed: " + e.Message);
        }
        finally
        {
            // release textures so repeated captures don't grow browser memory
            if (sized != null && sized != full)
            {
                Destroy(sized);
            }
            if (full != null)
            {
                Destroy(full);
            }
        }

        if (jpg == null || jpg.Length == 0)
        {
            Debug.LogWarning("[Capture] No JPG bytes, screenshot dropped");
            captureInProgress = false;
            yield break;
        }

        if (NetworkManager.Instance == null)
        {
            Debug.LogWarning("[Capture] No NetworkManager (quickTestMode?), screenshot not uploaded");
            captureInProgress = false;
            yield break;
        }

        NetworkManager.Instance.PushScreenshot(jpg, (success, message) =>
        {
            captureInProgress = false;
            if (!success)
            {
                Debug.LogWarning("[Capture] Screenshot upload failed: " + message);
            }
        });
    }

    // NEW: JSON snapshot of active units (teamID, sessionID, health, grid position) sent to the backend
    public void CaptureAndSendGameState()
    {
        if (!captureEnabled)
        {
            return;
        }

        if (NetworkManager.Instance == null)
        {
            //
            //
            //TestMode: no backend, log the snapshot locally
            string localJson = LoggingSystem.CaptureStateSnapshot("LOCAL", null);
            if (logStateSnapshotLocally)
            {
                Debug.Log("[Capture] Local state snapshot:\n" + localJson);
            }
            return;
        }

        string json = LoggingSystem.CaptureStateSnapshot(NetworkManager.Instance.teamID, NetworkManager.Instance.SessionID);
        if (logStateSnapshotLocally)
        {
            Debug.Log("[Capture] State snapshot:\n" + json);
        }

        NetworkManager.Instance.PushGameState(json, (success, message) =>
        {
            if (!success)
            {
                Debug.LogWarning("[Capture] State snapshot upload failed: " + message);
            }
        });
    }


    IEnumerator SimulationOverCleanUp(bool isWin) // UPDATED: win/loss flag
    {
        initTimeScaleBeforeTaunt = 0f;
        FastForwardUp(); // NEW: normal speed and VHS off before the end screen

        // UPDATED: results compiled the moment the game ends, pushed after the end screen wait
        string result = "";
        if (!quickTestMode)
        {
            result = LoggingSystem.Compile(isWin, NetworkManager.Instance.teamID, false, NetworkManager.Instance.SessionID); // UPDATED: no file save (WebGL)
        }

        SetTimeScale(0f, "end screen");

        interactionCharacter.StopPlayback();
        crt.ActivateEffect();
        crt.HideMessage(); // NEW: fade out leftover reinforcement text if the game ended mid-pause
        simulationOverPanel.SetActive(true);
        if (isWin)
        {
            winScreen.SetActive(true);
        }
        else
        {
            lossScreen.SetActive(true);
        }

        yield return new WaitForSecondsRealtime(sceneTransitionTime);

        if (quickTestMode)
        {
            yield break;
        }

        // NEW: push once, then return to the menu whether it worked or not
        bool pushDone = false;
        NetworkManager.Instance.PushJson(result, (success, message) =>
        {
            pushDone = true;
            if (!success)
            {
                Debug.LogWarning("[Results] push failed: " + message);
            }
        });
        float waited = 0f;
        while (!pushDone && waited < resultsPushMaxWait)
        {
            waited += Time.unscaledDeltaTime;
            yield return null;
        }
        if (!pushDone)
        {
            Debug.LogWarning("[Results] push still running after " + resultsPushMaxWait + "s, returning to menu");
        }
        SceneManager.LoadScene(0);
    }
    //Coins

    // NEW: show a new balance; animates only when it changed
    void UpdateCoins(int newCoins)
    {
        int oldCoins = coins;
        coins = newCoins;
        if (coinText == null)
        {
            return;
        }
        if (coinAnim != null)
        {
            StopCoroutine(coinAnim);
            coinAnim = null;
        }
        if (newCoins == oldCoins)
        {
            coinText.text = string.Format(coinFormat, newCoins);
            coinText.rectTransform.localScale = coinBaseScale;
            coinText.color = coinBaseColor;
            return;
        }
        coinAnim = StartCoroutine(CoinRoutine(oldCoins, newCoins));
    }

    // NEW: count from -> to with a scale pop and a gain/loss flash (unscaled time)
    IEnumerator CoinRoutine(int from, int to)
    {
        RectTransform rect = coinText.rectTransform;
        Color flashColor = coinGainColor;
        if (to < from)
        {
            flashColor = coinLossColor;
        }

        float time = 0f;
        while (time < coinCountTime)
        {
            time += Time.unscaledDeltaTime;
            float t = Mathf.Clamp01(time / coinCountTime);

            int shown = Mathf.RoundToInt(Mathf.Lerp(from, to, Mathf.SmoothStep(0f, 1f, t)));
            coinText.text = string.Format(coinFormat, shown);

            // grows then settles; flash fades back to the text's own color
            float pop = Mathf.Sin(t * Mathf.PI);
            rect.localScale = Vector3.Lerp(coinBaseScale, coinBaseScale * coinPopScale, pop);
            coinText.color = Color.Lerp(flashColor, coinBaseColor, t);
            yield return null;
        }

        coinText.text = string.Format(coinFormat, to);
        rect.localScale = coinBaseScale;
        coinText.color = coinBaseColor;
        coinAnim = null;
    }

    //Fast Forward
    // UPDATED: disabled for the competition: a different game speed changes the simulation
    // step, so the same agent could score differently. The button is hidden at start.
    public void FastForwardDown()
    {
    }
    public void FastForwardUp()
    {
        // NEW: player can't unfreeze a reinforcement pause
        if (phase == GamePhase.ReinforcementPause)
        {
            return;
        }
        SetTimeScale(1f, "FastForwardUP");
        vhs.Deactivate();
    }
}
