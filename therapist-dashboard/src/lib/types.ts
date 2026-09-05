export interface Therapist {
  id: string;
  name: string;
  institution: string;
  email: string;
}

export interface Case {
  id: string;
  name: string;
  surname: string;
  age: number;
  gender: "male" | "female";
  avatarColor: string;
  avatar?: string | null;
  lastSession: string;
  totalSessions: number;
  tabooTopics: string[];
  notes: string;
  birthYear?: string;
  birthPlace?: string;
  career?: string;
  family?: string;
  hobbies?: string;
  mode?: string;
  isActive?: boolean;
}

export interface Session {
  id: string;
  caseId: string;
  date: string;
  duration: number;
  status: "completed" | "in_progress" | "scheduled";
  rounds: number;
  emotionSummary: string;
  notes: string;
  mode?: string;
  score?: number;
  totalScore?: number;
  rating?: string;
  sessionNumber?: number;
  overallEmotion?: string;
  averageResponseTime?: string;
  storySummary?: string;
}

export interface RoundExchange {
  questionNumber: number;
  question: string;
  answer?: string;
  stage?: "pre_image" | null;
}

export interface SessionRound {
  id: string;
  sessionId: string;
  type: "心得" | "回合";
  roundNumber?: number;
  duration: number;
  sceneName: string;
  content: string;
  summary?: string;
  emotion: string;
  exchanges?: RoundExchange[];
  sceneImage?: string;
  // 情緒判斷依據：三維分數（0-100%）+ 訊號代碼陣列，見
  // therapist-dashboard/src/lib/emotionSignals.ts。舊資料（migration 前的
  // 回合）沒有這幾個欄位，會是 null/undefined。
  engagementPct?: number | null;
  happinessPct?: number | null;
  agitationPct?: number | null;
  signalCodes?: string[];
}

export interface ActiveSession {
  sessionId: string;
  caseId: string;
  caseName: string;
  currentRound: number;
  totalRounds: number;
  status: "running" | "paused" | "ended";
  currentScene: string;
  elderResponse: string;
  emotionState: "適當" | "亢奮" | "焦躁" | "低落" | "";
  responseTime: string;
  aiSuggestions: string[];
  tabooTopics: string[];
  // 長者剛講完話/心得、還卡在等治療師審核時會是 "pending_round" 或
  // "pending_closing"，平常是空字串。LiveSessionView 用這個欄位決定要不要
  // 顯示可編輯的審核框，以及按下確認時要打哪一支 API。
  reviewStatus: "" | "pending_round" | "pending_closing";
  elderResponseDraft: string;
  // 情緒判斷依據：三維分數（0-100%）+ 訊號代碼陣列，見
  // therapist-dashboard/src/lib/emotionSignals.ts。
  engagementPct: number;
  happinessPct: number;
  agitationPct: number;
  signalCodes: string[];
}
