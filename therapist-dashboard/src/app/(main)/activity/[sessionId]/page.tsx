"use client";

import { use, useState, useEffect } from "react";
import { LiveSessionView } from "./_components/LiveSessionView";
import { HistorySessionView } from "./_components/HistorySessionView";
import type { Session, SessionRound, Case } from "@/lib/types";

export default function ActivityPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = use(params);
  const [session, setSession] = useState<Session | null>(null);
  const [caseData, setCaseData] = useState<Case | null>(null);
  const [rounds, setRounds] = useState<SessionRound[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`/api/sessions/${sessionId}`)
      .then((r) => r.ok ? r.json() : null)
      .then(async (s) => {
        if (!s) { setLoading(false); return; }
        setSession(s);

        const [caseRes, roundsRes] = await Promise.all([
          fetch(`/api/cases/${s.caseId}`).then((r) => r.ok ? r.json() : null),
          fetch(`/api/sessions/${sessionId}/rounds`).then((r) => r.ok ? r.json() : []),
        ]);

        setCaseData(caseRes);
        setRounds(Array.isArray(roundsRes) ? roundsRes : []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [sessionId]);

  if (loading) return null;

  if (session && caseData) {
    return <HistorySessionView session={session} caseData={caseData} rounds={rounds} />;
  }

  return <LiveSessionView sessionId={sessionId} />;
}
