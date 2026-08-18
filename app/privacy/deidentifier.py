"""
個人資料脫敏模組。

⚠️ 重要原則：
   1. LLM (本地 Ollama) 收的是「原始」個人資料(本地安全)
   2. Stability AI (雲端) 收的是「脫敏後」資料
   3. 脫敏發生在「呼叫 Stability 之前」的最後一道關卡

2026-08-17：姓名移除改用 CKIP NER（中研院中文命名實體辨識），取代原本
「姓氏字+1-2字」的 regex heuristic。原本的 regex 撞詞：常見姓氏字（任、何、
高、方、金、田、白、林、常…）剛好也是一般詞彙的常用開頭字，「任何時候」
會被誤判成姓名、整段啃成「某人候」送進LLM生圖，這種誤傷沒辦法靠加白名單
根治（新詞還是會中招）。CKIP NER 用語意上下文判斷是不是真的人名，不是只
看字面組合，見 _get_ner_chunker／_remove_chinese_names 說明。

CKIP 模型載入不了（沒裝 ckip-transformers、沒網路抓不到模型）時，退回舊版
regex heuristic 當保底——脫敏是資安關卡，寧可退回較不精準的規則，也不能讓
整個脫敏功能掛掉。
"""
import re

try:
    from ckip_transformers.nlp import CkipNerChunker
except ImportError:  # pragma: no cover - 只在沒裝選用相依套件時觸發
    CkipNerChunker = None


class Deidentifier:
    """個人資料脫敏處理器。"""

    # 類別層級單例：CKIP 模型載入要花數秒＋數百MB記憶體，整個process只能
    # load一次，不能每次呼叫都重建（同樣考量見 Rag/ingest.py _get_cleaner()）。
    _ner_chunker = None
    _ner_load_failed = False

    def desensitize_profile(self, profile: dict) -> dict:
        """
        把整份個人資料脫敏。

        Args:
            profile: 從 user_profile_db 拿到的原始資料

        Returns:
            脫敏後的 dict,可安全送雲端 LLM 或 Stability AI
        """
        return {
            # 直接移除的欄位
            # name, family 完全不送

            # 模糊化的欄位
            "era": self._year_to_era(profile["birth_year"]),
            "region": self._region_to_city(profile["birth_place"]),
            "occupation_category": self._occupation_to_category(profile["main_occupation"]),

            # 可保留的欄位
            "preferences": profile["preferences"],
            "today_topic": profile["today_topic"],

            # 安全用:taboos 不送雲端,但內部要記得避開
            # (這份 dict 不會送雲端,只是 orchestrator 內部用)
            "_taboos_internal": profile["taboos"],
        }

    def desensitize_text(self, text: str, taboos: list[str] | None = None) -> str:
        """
        把一段文字脫敏(例如要送 Stability 的 prompt)。

        脫敏原則:移除「個體識別資訊」,保留「群體特徵」。
        - 移除:姓名、確切年份、具體鄉鎮街道、禁忌詞
        - 保留:產業類別、城市層級、年代範圍、主題、視覺元素

        Args:
            text: 要脫敏的文字
            taboos: 個人禁忌詞清單(也要過濾)

        Returns:
            脫敏後的文字
        """
        # 1. 移除確切年份(保留年代描述)
        text = self._remove_years(text)

        # 2. 模糊化具體鄉鎮街道(保留城市層級)
        text = self._fuzzify_locations(text)

        # 3. 移除個人禁忌詞
        if taboos:
            for taboo in taboos:
                text = text.replace(taboo, "")

        # 4. 移除中文姓名
        text = self._remove_chinese_names(text)

        # ⚠️ 注意:職業/產業類別「故意不移除」
        #    因為產業類別(如「紡織業」)是群體特徵,不識別個人,
        #    且對圖片生成有重要意義(否則 Stability 不知道要畫什麼場景)

        # 多餘空白整理
        text = re.sub(r"\s+", " ", text).strip()
        return text

    # ────── 內部方法 ──────

    def _year_to_era(self, year: int) -> str:
        """1945 → '1940 年代'"""
        decade = (year // 10) * 10
        return f"{decade} 年代"

    def _region_to_city(self, place: str) -> str:
        """
        '台中市西屯區' → '台中'
        '新北市淡水區' → '新北'
        '淡水' → '淡水'(已經是粗粒度)

        策略:取「市」之前的部分,如果沒有「市」就原樣回傳。
        """
        if "市" in place:
            return place.split("市")[0]
        if "縣" in place:
            return place.split("縣")[0]
        return place

    def _occupation_to_category(self, occupation: str) -> str:
        """
        把具體公司名改成產業類別。

        ⚠️ 注意:這個方法只處理「具體公司/工廠名」,
            不處理「職業類別」(如教師、農夫、家管),
            因為職業類別本身就是群體特徵,不識別個人。

        例子:
            '台中紡織廠' → '紡織業'          ✅ 移除具體公司
            '紡織業工人' → '紡織業工人'      ✅ 保留群體特徵
            '台積電工程師' → '半導體業工程師'  ✅ 移除具體公司
        """
        category_map = {
            "紡織廠": "紡織業",
            "電子廠": "電子業",
            "鋼鐵廠": "鋼鐵業",
            "造船廠": "造船業",
            "糖廠": "製糖業",
            "中華電信": "電信業",
            "台積電": "半導體業",
            "聯發科": "半導體業",
            "台塑": "石化業",
            "中油": "能源業",
            "台電": "能源業",
        }
        result = occupation
        for keyword, category in category_map.items():
            if keyword in result:
                result = result.replace(keyword, category)
        return result

    def _remove_years(self, text: str) -> str:
        """移除確切年份"""
        text = re.sub(r"(19|20)\d{2}\s*年", "從前", text)
        text = re.sub(r"民國\s*\d+\s*年", "從前", text)
        return text

    def _fuzzify_locations(self, text: str) -> str:
        """
        模糊化具體鄉鎮市區。
        匹配「XX 區/鄉/鎮/里」之類,移除細部行政區劃。
        """
        # 移除鄉鎮市區里村等細部地名
        text = re.sub(r"[一-龥]{1,4}[區鄉鎮里村]", "", text)
        # 路名/街名
        text = re.sub(r"[一-龥]{1,6}[路街道巷]\s*\d*\s*[號樓]?", "", text)
        return text

    def _get_ner_chunker(self):
        """
        Lazily 載入 CKIP NER 模型——只在第一次真的用到脫敏時才載入，不放在
        __init__ 或 app 啟動流程裡，避免完全用不到這條路徑（例如所有生圖都
        走 RAG／anchor、沒有長者原話需要脫敏）的情況還是要背這個載入成本。
        整個process只load一次（見類別層級 _ner_chunker 單例），失敗就記一次
        旗標，不要每次呼叫都重試載入拖慢速度。

        model="albert-base" 跟 Rag/ingest.py 的 CkipWordSegmenter／
        CkipPosTagger 用同一個模型家族，維持專案內CKIP用法一致、也比
        bert-base 輕量，device=-1 強制跑CPU（跟 Rag/ingest.py 同樣理由：
        這台機器GPU資源留給本機TTS/Ollama用，不跟NER推論搶）。
        """
        if Deidentifier._ner_chunker is not None or Deidentifier._ner_load_failed:
            return Deidentifier._ner_chunker
        if CkipNerChunker is None:
            print("[Deidentifier] 未安裝 ckip-transformers，姓名脫敏退回regex heuristic")
            Deidentifier._ner_load_failed = True
            return None
        try:
            Deidentifier._ner_chunker = CkipNerChunker(model="albert-base", device=-1)
        except Exception as e:
            print(f"[Deidentifier] CKIP NER模型載入失敗（{e!r}），姓名脫敏退回regex heuristic")
            Deidentifier._ner_load_failed = True
            return None
        return Deidentifier._ner_chunker

    def _remove_chinese_names(self, text: str) -> str:
        """
        移除文字裡的中文人名，改用 CKIP NER 判斷真正的人名實體（見
        _get_ner_chunker 說明），不再靠「姓氏字+1-2字」的字面組合猜測——
        後者會把「任何」「段落」這類剛好姓氏字開頭的一般詞彙也當人名誤刪。

        CKIP 模型載入或推論失敗時退回 _remove_chinese_names_regex_fallback
        當保底，不完美（可能誤傷一般詞彙）但至少功能不會整個掛掉。
        """
        if not text.strip():
            return text
        chunker = self._get_ner_chunker()
        if chunker is None:
            return self._remove_chinese_names_regex_fallback(text)
        try:
            tokens = chunker([text])[0]
        except Exception as e:
            print(f"[Deidentifier] CKIP NER推論失敗（{e!r}），這段文字退回regex heuristic")
            return self._remove_chinese_names_regex_fallback(text)
        person_spans = [tok.idx for tok in tokens if tok.ner == "PERSON"]
        if not person_spans:
            return text
        # 從後往前替換，避免前面替換動到字串長度後，後面span的字元位置跟著跑掉。
        for start, end in sorted(person_spans, key=lambda span: span[0], reverse=True):
            text = text[:start] + "某人" + text[end:]
        return text

    def _remove_chinese_names_regex_fallback(self, text: str) -> str:
        """
        舊版「姓氏字+1-2字」regex heuristic，只在CKIP模型載入/推論失敗時
        當保底使用——⚠️ 這個方法不完美，可能誤傷一些非姓名的詞（例如
        「任何」「段落」），見 _remove_chinese_names 的說明。
        """
        common_surnames = (
            "王李張劉陳楊黃趙吳周徐孫朱馬胡郭林何高梁鄭羅宋謝唐韓"
            "曹許鄧蕭馮曾程蔡彭潘袁于董余蘇葉呂魏蔣田杜丁沈姜范"
            "江傅鍾盧汪戴崔任陸廖姚方金邱夏譚韋賈鄒石熊孟秦閻薛"
            "侯雷白龍段郝孔邵史毛常萬顧賴武康賀嚴尹錢施牛洪龔"
        )
        pattern = f"[{common_surnames}][一-龥]{{1,2}}"
        return re.sub(pattern, "某人", text)
