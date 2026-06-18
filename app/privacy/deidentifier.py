"""
個人資料脫敏模組。

⚠️ 重要原則：
   1. LLM (本地 Ollama) 收的是「原始」個人資料(本地安全)
   2. Stability AI (雲端) 收的是「脫敏後」資料
   3. 脫敏發生在「呼叫 Stability 之前」的最後一道關卡

⚠️ MVP 版本說明：
   目前用純 Regex,涵蓋 80% 情況。
   之後可升級加入 CKIP NER (中研院中文命名實體辨識) 處理姓名/組織等開放領域實體。
"""
import re
from services.user_profile_client import UserProfile


class Deidentifier:
    """個人資料脫敏處理器。"""

    def desensitize_profile(self, profile: UserProfile) -> dict:
        """
        把整份個人資料脫敏。
        
        Args:
            profile: 從 user_profile_client 拿到的原始資料
        
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
        text = re.sub(r"[\u4e00-\u9fa5]{1,4}[區鄉鎮里村]", "", text)
        # 路名/街名
        text = re.sub(r"[\u4e00-\u9fa5]{1,6}[路街道巷]\s*\d*\s*[號樓]?", "", text)
        return text

    def _remove_chinese_names(self, text: str) -> str:
        """
        簡易中文姓名移除(常見姓氏 + 1-2 個字)。
        ⚠️ 這個方法不完美,可能誤傷一些非姓名的詞。
            MVP 階段可接受,之後升級成 CKIP NER 會更準。
        """
        common_surnames = (
            "王李張劉陳楊黃趙吳周徐孫朱馬胡郭林何高梁鄭羅宋謝唐韓"
            "曹許鄧蕭馮曾程蔡彭潘袁于董余蘇葉呂魏蔣田杜丁沈姜范"
            "江傅鍾盧汪戴崔任陸廖姚方金邱夏譚韋賈鄒石熊孟秦閻薛"
            "侯雷白龍段郝孔邵史毛常萬顧賴武康賀嚴尹錢施牛洪龔"
        )
        pattern = f"[{common_surnames}][\u4e00-\u9fa5]{{1,2}}"
        text = re.sub(pattern, "某人", text)
        return text