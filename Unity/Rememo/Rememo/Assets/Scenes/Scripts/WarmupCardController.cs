using System.Collections.Generic;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.UI;

public class WarmupCardController : MonoBehaviour
{
    [System.Serializable]
    public class ActionCard
    {
        public Sprite image;
        public float durationSeconds = 8f;
    }

    [Header("動作卡題庫，會從裡面隨機抽 5 張、不重複")]
    public ActionCard[] cardPool;

    [Header("UI 元件")]
    public Image cardImage;

    private List<ActionCard> selectedCards;
    private int currentIndex;

    void Start()
    {
        selectedCards = DrawRandomCards(cardPool, 5);
        currentIndex = 0;
        ShowCurrentCard();
    }

    List<ActionCard> DrawRandomCards(ActionCard[] pool, int count)
    {
        var shuffled = new List<ActionCard>(pool);
        for (int i = shuffled.Count - 1; i > 0; i--)
        {
            int j = Random.Range(0, i + 1);
            (shuffled[i], shuffled[j]) = (shuffled[j], shuffled[i]);
        }
        return shuffled.GetRange(0, Mathf.Min(count, shuffled.Count));
    }

    void ShowCurrentCard()
    {
        var card = selectedCards[currentIndex];
        cardImage.sprite = card.image;

        CancelInvoke(nameof(NextCard));
        Invoke(nameof(NextCard), card.durationSeconds);
    }

    void NextCard()
    {
        currentIndex++;
        if (currentIndex >= selectedCards.Count)
        {
            // 5 張暖身動作卡都做完了，接著跟 WarmupController.OnStart() 原本的行為
            // 一樣直接切去 InstructionScene，不經過 LoadingScene，避免長者多等一段
            // 讀取畫面。
            SceneManager.LoadScene("InstructionScene");
            return;
        }
        ShowCurrentCard();
    }
}
