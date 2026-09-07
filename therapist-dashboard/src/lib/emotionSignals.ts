// 情緒判斷依據共用常數：即時畫面／歷史回合／回合詳情三個頁面都從這裡讀，
// 避免顏色跟文案各自維護出現不一致（過去 LiveSessionView 跟
// HistorySessionView 的 EMOTION_COLORS/EMOTION_DOT 對 亢奮/焦躁 的顏色是
// 對調的，這裡統一採用即時頁原本的對應）。
//
// 後端（app/routers/sensor.py _reasoning_signals）只送訊號代碼字串，中文
// 文案跟圖示分類完全交給這裡的 SIGNAL_TEXT，不在後端重複維護。

export const EMOTION_COLORS: Record<string, string> = {
  適當: "#34c759",
  亢奮: "#f0c52c",
  焦躁: "#fb2c36",
  低落: "#888888",
};

export const DIMENSION_COLORS = {
  engagement: "#5b8ac5",
  happiness: "#34c759",
  agitation: "#e09540",
} as const;

export type SignalCategory = "face" | "eye" | "body" | "speaker";

export const SIGNAL_TEXT: Record<string, { category: SignalCategory; text: string }> = {
  face_not_detected: { category: "face", text: "未偵測到臉部畫面" },
  face_smile: { category: "face", text: "有微笑" },
  face_smile_slight: { category: "face", text: "略帶微笑" },
  face_frown: { category: "face", text: "沒有明顯笑容" },
  face_mouth_moved: { category: "face", text: "嘴巴有動作，像在說話" },
  eye_looking_away: { category: "eye", text: "視線游移，常看向別處" },
  eye_closed_drowsy: { category: "eye", text: "雙眼閉合，可能在打瞌睡" },
  body_lean_forward: { category: "body", text: "身體明顯前傾，投入互動" },
  body_lean_back: { category: "body", text: "身體微向後仰" },
  body_head_drop: { category: "body", text: "頭部低垂" },
  body_shoulder_raise: { category: "body", text: "肩膀聳起" },
  body_constricted: { category: "body", text: "手臂收緊貼近身體，姿勢封閉" },
  body_sway_high: { category: "body", text: "身體晃動明顯" },
  body_left_seat: { category: "body", text: "已離開偵測範圍，可能離座" },
  speaker_speaking: { category: "speaker", text: "有開口說話" },
  speaker_pitch_var_high: { category: "speaker", text: "語調起伏明顯" },
};

export type Dimension = "engagement" | "happiness" | "agitation";

// 三個量表用不同的三段式措辭，不是共用同一組「偏高/中等/偏低」——表情訊號
// 量出來的是 valence（方向），「偏高」讀起來像在講訊號強度而不是正負向，改用
// 偏正向/中性/偏負向；肢體與語調量出來的是 arousal（活動量），改用波動明顯/
// 中等/平穩，跟「表情」「肢體與語調」這種不帶方向的軸名搭配才不會誤讀成
// 「量到多少訊號」。專注度本身就是一個有高低的程度詞，維持偏高/中等/偏低。
export function tierLabel(pct: number, dimension: Dimension = "engagement"): string {
  if (dimension === "happiness") {
    if (pct >= 67) return "偏正向";
    if (pct >= 34) return "中性";
    return "偏負向";
  }
  if (dimension === "agitation") {
    if (pct >= 67) return "波動明顯";
    if (pct >= 34) return "中等";
    return "平穩";
  }
  if (pct >= 67) return "偏高";
  if (pct >= 34) return "中等";
  return "偏低";
}

// 情緒判斷依據的一句話說明：Kinect 的四分類實際上是看肢體與語調（arousal）
// 跟表情訊號（valence）這兩軸組合出來的，不是三條量表各自獨立算分（sensor.py
// _classify_from_scores）；只列三條量表看不出這個組合關係，所以在最終判斷
// 旁邊補這一句，只講這兩個真正決定分類的軸，不重複列專注度（它只在低激動、
// 表情中性的模糊地帶才當裁判，多數情況下不是決定性的兩軸之一）。
// 用詞直接沿用 tierLabel 已經在量表上顯示的字，不是另外重新判斷一次門檻。
export function reasonCaption(happinessPct: number, agitationPct: number): string {
  return `肢體與語調${tierLabel(agitationPct, "agitation")}、表情${tierLabel(happinessPct, "happiness")}`;
}

// 每個訊號代碼實際影響哪一個量表，對應 app/routers/sensor.py 的加權公式
// （非猜測——face_happy 進 _face_happiness 影響表情訊號；looking_away/
// eye_closed/lean/head_drop/speaking 進 _face_engagement/_skel_engagement/
// audio_eng 影響專注度；body_sway/pitch_variance/shoulder_raise 進 raw_agi
// 影響肢體與語調訊號），用來把訊號標籤掛在對應的量表下面，讓「為什麼這個
// 量表這麼高/低」看得出因果關係，不是三條量表講完後另外丟一坨標籤在旁邊。
//
// body_shoulder_raise 歸在「肢體與語調」（agitation），不是「表情訊號」
// （happiness）：查證過的心理生理學文獻——斜方肌肌電活動是被學界認可的
// 壓力/激動程度量測方式，跟身體晃動、音高變異是同一類「激動程度」訊號，
// 不是「正不正向」的訊號（見 sensor.py `_skel_tension` 的完整討論）。
// 已知限制：這個系統操作介面要求長者舉手控制游標，維持舉手動作本身就會
// 讓肩關節追蹤位置抬高（姿態辨識文獻裡的 "motion artifact"），所以後端
// 給這個訊號的加權比重刻意壓低（0.15，見 sensor.py raw_agi），只是緩解
// 不是根治。
//
// 2026-09-05 命名調整：「愉悅度」「緊繃度」改成「表情訊號」「肢體與語調訊號」
// ——這兩個是從 AU/骨架晃動+語調變化推論出來的「情緒特質」命名，但今天把
// 負向表情 AU 集合擴大成同時涵蓋悲傷型跟憤怒型（見 sensor.py
// AU_VALENCE_NEGATIVE）之後，「愉悅度：偏低」沒辦法分辨是悲傷還是憤怒，
// 容易讓人誤以為只跟「快不快樂」有關；「緊繃度」同理只暗示負向的焦慮，
// 沒辦法涵蓋「亢奮」這種正向的高激動。改成「這條數據是從哪個管道量出來的」
// （表情／肢體語調）而不是「量出來的是什麼心理狀態」，正負向判斷交給下面的
// 訊號小標籤跟最終分類結果去講，不讓量表名稱自己先劇透。
export const SIGNAL_DIMENSION: Record<string, Dimension> = {
  eye_looking_away: "engagement",
  eye_closed_drowsy: "engagement",
  body_lean_forward: "engagement",
  body_lean_back: "engagement",
  body_head_drop: "engagement",
  face_mouth_moved: "engagement",
  speaker_speaking: "engagement",
  // 離座不是加權公式的一部分（見 sensor.py _reasoning_signals 的說明），但
  // 對「為什麼專注度這麼低」是關鍵情境資訊，歸進專注度底下一起顯示。
  body_left_seat: "engagement",
  // 身體收縮/封閉姿勢（C 階段草稿）：也不是任何一條量表加權公式的一部分，
  // 是 _classify_from_scores 判斷「低落」的加強確認條件（2026-09-06 改成
  // 要跟 engagement 退縮同時成立的 and 關係，查證失智/淡漠評估文獻後認為
  // 單一姿勢訊號證據力不足以獨立否決，見 sensor.py 的完整說明），功能上跟
  // engagement 退縮是同一類角色，所以歸在這裡顯示。
  body_constricted: "engagement",
  // happiness 100% 是 _face_happiness(au)（聳肩已經改進 agitation，表情訊號
  // 不再摻任何骨架資料），比「表情訊號」以外任何量表都更依賴臉部資料
  // （engagement 只有 30% 權重來自臉），所以歸在這裡而不是 engagement。
  // 2026-09-06 前：沒偵測到臉時這個百分比會被當中性（0分）拉進平均，這個
  // 標籤是用來提醒治療師「這次百分比不可信」；改成沒偵測到臉就不更新
  // happiness 的 EMA（見 sensor.py _ema_classify）之後，百分比只反映真的
  // 偵測到臉的那些幀，不會再被稀釋，這個標籤現在單純是「這回合期間有
  // 追丟臉部的情況」的情境資訊，不影響百分比本身的可信度。
  face_not_detected: "happiness",
  face_smile: "happiness",
  face_smile_slight: "happiness",
  face_frown: "happiness",
  body_shoulder_raise: "agitation",
  body_sway_high: "agitation",
  speaker_pitch_var_high: "agitation",
};

export function groupSignalsByDimension(codes: string[]): Record<Dimension, string[]> {
  const groups: Record<Dimension, string[]> = { engagement: [], happiness: [], agitation: [] };
  for (const code of codes) {
    const dim = SIGNAL_DIMENSION[code];
    if (dim) groups[dim].push(code);
  }
  return groups;
}
