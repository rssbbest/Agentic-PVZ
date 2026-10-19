using TMPro;
using UnityEngine;

public class UIController : MonoBehaviour
{
    [Header("CRT effect")]
    [SerializeField] CRTMenu crt;


    [Header("UI controls")]
    [SerializeField] TextMeshProUGUI errorText;
    [SerializeField] Animator errorAnimator;
    [SerializeField] AnimationClip errorAnim;
    

    string teamId = "";
    private void Start()
    {
        Time.timeScale = 1f;
        crt.ActivateEffect();
    }

    

    public void ChangeTeamId(string teamID)
    {
        teamId = teamID;
    }
    public string GetTeamID()
    {
        return teamId;
    }
    public void DisplayError(string error)
    {
        errorText.gameObject.SetActive(true);
        errorText.alpha = 255;
        errorAnimator.Play(errorAnim.name,0,0f);
        errorText.text = error; 
    }

    public void Submit()
    {
        NetworkManager.Instance.SubmitTeamID(teamId);
    }



    // DEBUG DATA

    public string DEBUG_url_team_data = "";
    public string DEBUG_url_results= "";
    public string DEBUG_url_reinforcements= "";
    public void DEBUG_TEAM_DATA_URL(string url)
    {
        DEBUG_url_team_data = url;
    }
    public void DEBUG_RESULTS_URL(string url)
    {
        DEBUG_url_results = url;
    }

    public void DEBUG_REINFORCEMENTS_URL(string url)
    {
        DEBUG_url_reinforcements = url;
    }
}
