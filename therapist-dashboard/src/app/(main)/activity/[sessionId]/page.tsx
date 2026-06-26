"use client";

import { use } from "react";
import { mockSessions, mockSessionRounds, mockCases } from "@/lib/mock-data";
import { LiveSessionView } from "./_components/LiveSessionView";
import { HistorySessionView } from "./_components/HistorySessionView";

export default function ActivityPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = use(params);

  const completedSession = mockSessions.find((s) => s.id === sessionId) ?? null;
  const caseData = completedSession
    ? (mockCases.find((c) => c.id === completedSession.caseId) ?? null)
    : null;
  const rounds = mockSessionRounds.filter((r) => r.sessionId === sessionId);

  if (completedSession && caseData) {
    return <HistorySessionView session={completedSession} caseData={caseData} rounds={rounds} />;
  }

  return <LiveSessionView />;
}
