"""
Face Emotion 服務：用 py-feat 分析單張畫面的 FACS Action Unit (AU) 強度，
取代 Kinect 內建 Face API（只有 8 個粗糙布林屬性）。

設計原則（見 project_openface_kinect_emotion_redesign 決策記錄）：
- 只負責「臉」這一軸的訊號萃取，回傳原始 AU 強度，情緒判斷邏輯完全留在
  app/routers/sensor.py（維持現有「感測跟解讀分開」的架構）。
- 沒偵測到臉/畫面品質太差時回傳 face_detected=false，呼叫端（app 服務）要能
  正常處理「這次沒有臉部資料」，不把這裡當成一定會成功的服務。
"""
import io
import logging
import os
import tempfile
from contextlib import asynccontextmanager

# 這個服務完全不用GPU（見下面 _load_detector 的說明），必須在 import torch
# 之前設定 CUDA_VISIBLE_DEVICES——CUDA可見性是 torch 的C擴充在初始化時讀
# 一次的，import之後再設定環境變數沒有效果。
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

import torch  # noqa: E402（見上面說明，必須排在設定 CUDA_VISIBLE_DEVICES 之後）
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 部分 py-feat 模型（例如 img2pose 的 resnet18 backbone）checkpoint 存檔時是
# CUDA tensor，torch.load 在完全看不到CUDA裝置時預設仍會嘗試反序列化回CUDA
# 裝置、直接拋 RuntimeError，跟 feat.Detector 傳的 device 參數無關，要在
# torch.load 這一層強制指定 map_location 才能繞過。monkeypatch 不能只用
# kwargs.setdefault：torch.hub.load_state_dict_from_url 內部呼叫時會明確傳
# map_location=None（不是省略這個key），setdefault對「key存在、值是None」
# 不會生效，要顯式檢查並覆蓋。
_original_torch_load = torch.load


def _cpu_only_torch_load(*args, **kwargs):
    if len(args) < 2 and kwargs.get("map_location") is None:
        kwargs["map_location"] = "cpu"
    return _original_torch_load(*args, **kwargs)


torch.load = _cpu_only_torch_load

_detector = None  # 延遲載入，Start-up 時初始化一次


def _load_detector():
    """載入 py-feat Detector。模型權重第一次執行會自動從 HuggingFace 下載，
    掛了 volume 快取後之後啟動不用重下。

    device="cpu"，完全不佔用GPU——這台機器的GPU同時被 ollama（LLM＋RAG
    嵌入模型，100% GPU 常駐）、stt 共用，VRAM餘裕壓到只剩約1.6GB，懷疑是
    偶爾單次LLM生成卡到數分鐘的原因之一。CPU代價：Unity端每2秒才送一次
    畫面做分析（不是即時30fps影像流，見 app/routers/sensor.py 的
    sendInterval 說明），單張畫面分析在CPU上約0.81秒、GPU上約0.5-0.6秒，
    差距遠低於2秒的呼叫間隔，不會造成堆積；換來的是釋放約970MB VRAM。
    au_model="xgb" 本身是XGBoost，原本就不吃GPU加速，真正受益於GPU的只有
    另外4個小型CNN模型，CPU推論的代價本來就有限。

    只改 device="cpu"、但容器仍能看到GPU（NVIDIA_VISIBLE_DEVICES）不夠——
    只要容器看得到CUDA裝置，torch/py-feat內部某些初始化路徑還是會建立CUDA
    context、佔掉VRAM。必須連容器的GPU可見性一起拿掉（上面的
    CUDA_VISIBLE_DEVICES=""＋torch.load monkeypatch），才會真的釋放VRAM；
    docker-compose.yml 對應的NVIDIA環境變數／GPU device reservation也要
    一併移除。
    """
    from feat import Detector

    logger.info("載入 py-feat Detector...")
    detector = Detector(
        face_model="retinaface",
        landmark_model="mobilefacenet",
        au_model="xgb",
        emotion_model="resmasknet",
        facepose_model="img2pose",
        device="cpu",
    )
    logger.info("py-feat Detector 載入完成")
    return detector


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _detector
    _detector = _load_detector()
    yield


app = FastAPI(title="Face Emotion (py-feat) Service", lifespan=lifespan)


class AnalyzeResult(BaseModel):
    face_detected: bool
    aus: dict[str, float] = {}
    pose: dict[str, float] = {}  # Pitch/Roll/Yaw，可能為空（模型沒給則不填）


@app.get("/health")
async def health():
    return {"ok": _detector is not None}


@app.post("/analyze", response_model=AnalyzeResult)
async def analyze(frame: UploadFile = File(...)):
    if _detector is None:
        raise HTTPException(status_code=503, detail="Detector 尚未載入完成")

    raw = await frame.read()
    try:
        image = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail="無法解析畫面（不是有效的圖片格式）")

    # py-feat 的 detect_image 目前吃檔案路徑最穩，直接用暫存檔避免跟不同版本的
    # in-memory API 兜不起來。單張圖片分析很快，暫存檔 I/O 不是效能瓶頸。
    with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
        image.save(tmp.name, format="JPEG")
        try:
            result = _detector.detect_image(tmp.name)
        except Exception as e:
            logger.warning(f"[face-service] detect_image 失敗（可能是沒偵測到臉）: {e}")
            return AnalyzeResult(face_detected=False)

    if result is None or len(result) == 0:
        return AnalyzeResult(face_detected=False)

    row = result.iloc[0]

    # 動態抓 AU* 欄位，不寫死確切欄位名稱清單——不同版本/au_model 給的 AU 集合
    # 可能略有出入，這裡盡量拿到多少算多少，映射邏輯（app/routers/sensor.py）
    # 只會用到它明確需要的幾個 key（AU06/AU12 等），拿不到的話會是空字串/None。
    aus: dict[str, float] = {}
    for col in result.columns:
        if col.upper().startswith("AU"):
            val = row[col]
            if val is not None and not (isinstance(val, float) and np.isnan(val)):
                aus[col.upper()] = float(val)

    pose: dict[str, float] = {}
    for col in ("Pitch", "Roll", "Yaw"):
        if col in result.columns:
            val = row[col]
            if val is not None and not (isinstance(val, float) and np.isnan(val)):
                pose[col] = float(val)

    if not aus:
        # AU 欄位全空，通常代表這一幀沒有偵測到臉（py-feat 對沒臉的 frame
        # 常見做法是整列填 NaN，不是丟例外）。
        return AnalyzeResult(face_detected=False)

    return AnalyzeResult(face_detected=True, aus=aus, pose=pose)
