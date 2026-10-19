using TMPro;
using UnityEngine;
using System.Collections;
using System.Collections.Generic;
using System.Data;


public class DamageVisualizerTextHandler : MonoBehaviour
{
    [Header("TextSettings")]
    [SerializeField] float randomWidth;
    [SerializeField] float randomHeight;
    [SerializeField] float reactionRate;
    [SerializeField] float stayTime;
    [SerializeField] Color defaultColor;

    [SerializeField] List<TextMeshPro> textList = new List<TextMeshPro>();
    public void SpawnText(float damage,Vector3 pos)
    {
        TextMeshPro text = GetFreeText();

        Vector3 randomNoisePos = new Vector3(Random.Range(-randomWidth,randomWidth), Random.Range(-randomHeight, randomHeight),0);

        text.transform.position = pos + randomNoisePos;
    
        text.text = $"{damage}";

        StartCoroutine(StartSquence(text));
    }

    IEnumerator StartSquence(TextMeshPro text)
    {


        EnableSequence(text);

        float alpha = 0;
        Debug.Log($"alpha");
        for (; alpha < 240;)
        {
            alpha += reactionRate;
            text.alpha = alpha;
            yield return new WaitForSeconds(Time.deltaTime);
        }

        yield return new WaitForSeconds(stayTime);



        //disable
        alpha = 255;
        for (; alpha > 0;)
        {
            alpha -= reactionRate;
            text.alpha = alpha;
            yield return new WaitForSeconds(Time.deltaTime);
        }

        DisableSequence(text);
    }

    TextMeshPro GetFreeText()
    {
        foreach(TextMeshPro text in textList)
        {
            if (!text.gameObject.activeSelf)
            {
                return text;
            }
        }
        return textList[0];
    }

    void EnableSequence(TextMeshPro text)
    {
        text.color = defaultColor;
        text.gameObject.SetActive(true);
    }
    void DisableSequence(TextMeshPro text)
    {
        text.text = "";
        text.color = defaultColor;
        text.gameObject.SetActive(false);

    }


}
