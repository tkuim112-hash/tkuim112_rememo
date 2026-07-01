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
}

export interface SessionRound {
  id: string;
  sessionId: string;
  type: "心得" | "回合";
  roundNumber?: number;
  duration: number;
  sceneName: string;
  content: string;
  emotion: string;
  exchanges?: RoundExchange[];
  sceneImage?: string;
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
  emotionState: "適當" | "亢奮" | "焦躁" | "低落";
  responseTime: string;
  aiSuggestions: string[];
  tabooTopics: string[];
}
