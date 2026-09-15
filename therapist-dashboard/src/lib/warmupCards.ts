// 暖身動作卡的圖片/文字對照表：Unity 只回報 card_key 這個穩定識別碼，圖片/
// 文字存在前端本機（見 Unity WarmupCardController.ActionCard.cardKey 說明）。
// 這份表原本只有 warmup/page.tsx 一份，現在 warmup-summary/page.tsx 也要用
// 同一套 card_key 對照圖片/文字，抽成共用模組避免兩邊各自維護、之後漏改。
//
// metricOneLabel：暖身狀態總覽頁第一列指標的標題，依這張卡實際量測的東西
// 而不同——大部分卡是真的角度型（肩外展/髖屈曲/軀幹旋轉/肩水平外展），但
// 摸膝蓋量的是距離、手臂旋轉量的是圓形吻合度，掛「關節角度達成率」不準確。
export const WARMUP_CARDS: Record<
  string,
  { label: string; image: string; steps: number; metricOneLabel: string }
> = {
  arm_raise: { label: "手臂平舉 5 秒", image: "/image/warmup/arm_raise.png", steps: 5, metricOneLabel: "關節角度達成率" },
  leg_kick: { label: "踢腿 3 次", image: "/image/warmup/leg_kick.png", steps: 3, metricOneLabel: "關節角度達成率" },
  march_in_place: { label: "原地踏步 5 次", image: "/image/warmup/march_in_place.png", steps: 5, metricOneLabel: "關節角度達成率" },
  touch_knees: { label: "手摸膝蓋 5 次", image: "/image/warmup/touch_knees.png", steps: 5, metricOneLabel: "動作到位程度" },
  arm_circle: { label: "手臂旋轉 5 次", image: "/image/warmup/arm_circle.png", steps: 5, metricOneLabel: "畫圓穩定度" },
  waist_twist: { label: "扭腰 5 次", image: "/image/warmup/waist_twist.png", steps: 5, metricOneLabel: "關節角度達成率" },
  chest_expand: { label: "擴胸 5 次", image: "/image/warmup/chest_expand.png", steps: 5, metricOneLabel: "關節角度達成率" },
};
