"""
圖片生成服務客戶端 — 呼叫 Stability AI 雲端 API。

流程:
  1. 接收已脫敏的 prompt
  2. 呼叫 Stability AI 的 Stable Image Core (1:1 比例 → 1024×1024)
  3. resize 到 700×700 存檔
  4. 回傳本地檔案路徑

⚠️ Stability API key 透過 .env 設定,絕不寫死在程式碼。
"""
import httpx
from io import BytesIO
from pathlib import Path
from PIL import Image
from config import settings


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
        resized = self._resize_to_700(image_bytes)
        
        # 3. 存到本地
        session_dir = self.output_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        output_path = session_dir / f"round_{round_number}.png"
        output_path.write_bytes(resized)
        
        return str(output_path)

    def _resize_to_700(self, image_bytes: bytes) -> bytes:
        """1024×1024 → 700×700,用 LANCZOS 演算法(縮圖品質最好)"""
        img = Image.open(BytesIO(image_bytes))
        if img.mode != "RGB":
            img = img.convert("RGB")
        img = img.resize((700, 700), Image.LANCZOS)
        output = BytesIO()
        img.save(output, format="PNG", optimize=True)
        return output.getvalue()

    async def close(self):
        await self.client.aclose()