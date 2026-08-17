"""
圖片生成服務客戶端。

兩個實作，介面一致（generate/close），main.py 的 app.state.image_service
注入哪一個就用哪一個，orchestrator.py 不用因為換供應商改呼叫方式：
  - StabilityImageService：Stability AI Stable Image Core
  - OpenAIImageService：OpenAI gpt-image-2（2026-08-14 換上，見該類別說明）

流程都一樣:
  1. 接收已脫敏的 prompt
  2. 呼叫雲端 API 生成 1024×1024 圖片
  3. resize 到 700×700 存檔
  4. 回傳本地檔案路徑

⚠️ API key 透過 .env 設定,絕不寫死在程式碼。
"""
import base64
import httpx
from io import BytesIO
from pathlib import Path
from PIL import Image
from config import settings


def _resize_to_700(image_bytes: bytes) -> bytes:
    """1024×1024 → 700×700,用 LANCZOS 演算法(縮圖品質最好)，兩個服務共用。"""
    img = Image.open(BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img = img.resize((700, 700), Image.LANCZOS)
    output = BytesIO()
    img.save(output, format="PNG", optimize=True)
    return output.getvalue()


class StabilityImageService:
    """Stability AI 客戶端,負責生成「群體特徵」級的場景圖。"""

    def __init__(self):
        self.api_key = settings.stability_api_key
        if not self.api_key:
            raise ValueError(
                "STABILITY_API_KEY 未設定,請檢查 .env 檔。"
            )
        
        # Stable Image Core 是價位最低的選項(約 $0.03/張),效果夠用
        self.url = "https://api.stability.ai/v2beta/stable-image/generate/core"
        
        # 圖片儲存目錄(在容器內是 /media/images,本機對應 ./media/images)
        self.output_dir = Path("/media/images")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Stability 生圖可能要 5-15 秒
        self.client = httpx.AsyncClient(timeout=60.0)

    async def generate(
        self,
        prompt: str,
        session_id: str,
        round_number: int,
        negative_prompt: str = "text, characters, letters, words, comic panels, multiple panels, grid layout, frames, borders, logo, signature, watermark, blurry, low quality",
    ) -> str:
        """
        生成一張圖,存到本地,回傳路徑。

        Args:
            prompt: 已脫敏的 prompt(由 LLM 規劃)
            session_id: 用於檔名歸類
            round_number: 第幾回合(1/2/3)
            negative_prompt: 不希望出現的元素

        Returns:
            本地檔案路徑,例如 "/media/images/sess_001/round_1.png"
        """
        # 1. 呼叫 Stability API
        response = await self.client.post(
            self.url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "image/*",
            },
            files={"none": ""},  # multipart/form-data 即使沒檔案也要這個欄位
            data={
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "aspect_ratio": "1:1",
                "output_format": "png",
            },
        )
        
        if response.status_code != 200:
            raise RuntimeError(
                f"Stability API 失敗 ({response.status_code}): {response.text}"
            )
        
        # 2. 取得圖片並 resize 成 700×700
        image_bytes = response.content
        resized = _resize_to_700(image_bytes)

        # 3. 存到本地
        session_dir = self.output_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        output_path = session_dir / f"round_{round_number}.png"
        output_path.write_bytes(resized)

        return str(output_path)

    async def close(self):
        await self.client.aclose()


class OpenAIImageService:
    """
    OpenAI Images API（gpt-image-2）客戶端，介面跟 StabilityImageService
    一致（generate/close 簽章相同），main.py 換注入哪一個，orchestrator.py
    完全不用改。

    2026-08-14 換上：先用 manual_test_gpt_image.py 驗證過畫質/prompt遵循度
    夠好、值得換掉Stability，這裡把驗證過的請求格式搬進正式服務。跟
    Stability 的介面差異都封裝在這個類別內部：
      1. endpoint 不同：POST https://api.openai.com/v1/images/generations
      2. body 要多帶 "model" 欄位（固定 "gpt-image-2"）
      3. 回傳格式不同：Stability 直接拿原始bytes；OpenAI 回傳 JSON，圖片
         資料在 data[0]["b64_json"]，是 base64 編碼字串，要先解碼。
      4. 沒有 negative_prompt 欄位——gpt-image-2 的 API 沒有對應參數
         （manual_test_gpt_image.py 驗證時就沒帶這個欄位），這裡收下
         negative_prompt 參數只是維持跟 StabilityImageService 相同的呼叫
         簽章，不會真的送出去，呼叫端不用因為換供應商特別判斷要不要傳。
    """

    MODEL = "gpt-image-2"

    def __init__(self):
        self.api_key = settings.openai_api_key
        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY 未設定,請檢查 .env 檔。"
            )

        self.url = "https://api.openai.com/v1/images/generations"

        self.output_dir = Path("/media/images")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.client = httpx.AsyncClient(timeout=120.0)

    async def generate(
        self,
        prompt: str,
        session_id: str,
        round_number: int,
        negative_prompt: str = "",
    ) -> str:
        """
        生成一張圖,存到本地,回傳路徑。介面同 StabilityImageService.generate，
        negative_prompt 收下但不會送給 OpenAI（見類別說明）。
        """
        response = await self.client.post(
            self.url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.MODEL,
                "prompt": prompt,
                "size": "1024x1024",
                "n": 1,
            },
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"OpenAI Images API 失敗 ({response.status_code}): {response.text}"
            )

        result = response.json()
        image_bytes = base64.b64decode(result["data"][0]["b64_json"])
        resized = _resize_to_700(image_bytes)

        session_dir = self.output_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        output_path = session_dir / f"round_{round_number}.png"
        output_path.write_bytes(resized)

        return str(output_path)

    async def close(self):
        await self.client.aclose()