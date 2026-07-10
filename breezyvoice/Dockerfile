# syntax=docker/dockerfile:1
FROM python:3.10-slim

WORKDIR /breezyvoice

# 系統套件：ffmpeg 給音訊/whisper 用，libsndfile1 給 soundfile 用，
# cmake/pkg-config/autoconf/libtool 給 pynini（WeTextProcessing 的依賴）萬一要 source build 用。
# 這個環境的網路不太穩定，常常在抓套件列表時 DNS 解析逾時，加大重試次數/逾時時間降低失敗機率。
# --no-install-recommends：headless server 用不到 ffmpeg 的硬體加速/GUI 相依套件
# (mesa 顯卡驅動、vulkan、X11 等一大串)，之前踩過 WSL2 記憶體(7.5GB)在裝這些的時候被 OOM killer 砍掉。
RUN apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 update \
    && apt-get install -y --no-install-recommends \
    git wget unzip ffmpeg libsndfile1 build-essential \
    cmake pkg-config autoconf libtool \
    && rm -rf /var/lib/apt/lists/*

# 下載 BreezyVoice 原始碼
# 用 `cp -a src/. .` 而不是 `mv src/* .`：後者是 shell 萬用字元，不會複製隱藏檔案
RUN wget -qO source.zip https://github.com/mtkresearch/BreezyVoice/archive/refs/heads/main.zip \
    && unzip -q source.zip \
    && cp -a BreezyVoice-main/. . \
    && rm -rf source.zip BreezyVoice-main

# RTX 50 系列 (Blackwell / sm_120) 需要 PyTorch nightly cu128，穩定版尚未支援。
# 官方 requirements.txt 釘的是 torch==2.3.1+cu118，這裡改用 nightly cu128；
# torchaudio 用 --no-deps 跳過它對 torch 精確版本的宣告檢查（避免 pip 版本解析衝突）。
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --pre torch \
      --index-url https://download.pytorch.org/whl/nightly/cu128 --default-timeout=1000 \
    && pip install --pre torchaudio --no-deps \
      --index-url https://download.pytorch.org/whl/nightly/cu128 --default-timeout=1000 \
    && pip freeze | grep -i '^torch==' > /tmp/torch-constraint.txt \
    && cat /tmp/torch-constraint.txt

# 官方 requirements.txt 拿掉了幾樣東西：
# - deepspeed：實際推論路徑（api.py -> single_inference.py -> cosyvoice/*）完全沒 import 到，是上游訓練用的殘留依賴
# - ttsfrd 那兩個 modelscope 的 wheel：cosyvoice/cli/frontend.py 用 try/except 包住，裝不到會自動 fallback 成 WeTextProcessing
#
# openai-whisper==20231117 的 setup.py 用到 pkg_resources，但 pip 幫它建立的隔離編譯環境
# 會抓最新版 setuptools（已經拿掉 pkg_resources），導致編譯失敗。
# 先裝一個還有 pkg_resources 的舊版 setuptools，並用 --no-build-isolation 讓它沿用這個版本，
# 不要讓 pip 另外建立一個隔離環境去抓最新版。
#
# conformer 等套件宣告依賴 torch（沒鎖版本），會讓 pip 想另外從一般 PyPI 抓一個
# 跟我們裝的 nightly cu128 版不同的 torch。用 -c 帶入 constraint file 鎖死用已安裝的版本。
#
# requirements.txt 裡把 openai-whisper==20231117 鬆綁成不鎖版本：
# 舊版釘死 triton<3，跟新版 torch 需要的 triton 3.x 衝突。single_inference.py 只在
# 使用者沒提供逐字稿時才會真的呼叫 whisper 做語音辨識，我們一定會自己傳逐字稿進去，
# 這裡只需要 import 得過，不需要鎖死那個舊版本號。
#
# requirements.txt 裡加了 ruamel.yaml<0.19.0：HyperPyYAML 1.2.2 跟 ruamel.yaml 0.19+
# 不相容（Composer.compose_node 會去檢查 self.loader.max_depth，舊版 hyperpyyaml
# 用的 Loader 沒有這個屬性），是 CosyVoice 系列已知的問題，鎖舊版 ruamel.yaml 解決。
COPY requirements.txt /breezyvoice/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install "setuptools<81" wheel \
    && pip install --no-build-isolation -c /tmp/torch-constraint.txt -r requirements.txt --default-timeout=1000

# 修補已知 bug：torchaudio 新版拿掉了 set_audio_backend()（前面查過的 CosyVoice #1815
# 提到的 torchaudio API 變動），cosyvoice/dataset/processor.py 在 import 時呼叫這個已
# 移除的函式會直接炸掉。這個檔案是給訓練用的資料前處理，我們做推論用不到，直接拿掉這行。
RUN sed -i "/torchaudio.set_audio_backend('soundfile')/d" cosyvoice/dataset/processor.py

# 修補已知 bug：新版 torchaudio.load() 預設改用 TorchCodec 當後端，但我們沒裝 torchcodec，
# 會直接 ImportError。改用 soundfile 直接讀檔，跟 Fish Speech 那邊用的是同一招。
RUN sed -i \
    -e '/^import torchaudio$/a import torch\nimport soundfile as sf' \
    -e "s/speech, sample_rate = torchaudio.load(wav)/waveform_np, sample_rate = sf.read(wav); speech = torch.from_numpy(waveform_np).float().t() if waveform_np.ndim > 1 else torch.from_numpy(waveform_np).float().unsqueeze(0)/" \
    cosyvoice/utils/file_utils.py

# 同樣的 torchcodec 問題，api.py 裡輸出音檔存檔也要 patch（load 跟 save 兩個方向都要處理）。
RUN sed -i \
    -e '/^import torchaudio$/a import soundfile as sf' \
    -e 's/torchaudio.save(audio_buffer, output\["tts_speech"\], 22050, format="wav")/sf.write(audio_buffer, output["tts_speech"].squeeze(0).cpu().numpy(), 22050, format="WAV")/' \
    api.py

EXPOSE 8080

CMD ["python3", "api.py"]
