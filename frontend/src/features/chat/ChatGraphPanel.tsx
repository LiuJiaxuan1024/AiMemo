import { useEffect, useState } from "react";

import { ChatGraphWorkspace } from "../chat_view/ChatGraphWorkspace";
import { getTurnStateHistory } from "./chatApi";
import type { ChatTurnGraph, ChatTurnStateHistory } from "./types";

interface ChatGraphPanelProps {
  graph: ChatTurnGraph | null;
  isClosing?: boolean;
  isLoading: boolean;
  onClose: () => void;
}

export function ChatGraphPanel({
  graph,
  isClosing = false,
  isLoading,
  onClose,
}: ChatGraphPanelProps) {
  const [stateHistory, setStateHistory] = useState<ChatTurnStateHistory | null>(null);
  const [historyError, setHistoryError] = useState("");
  const [isHistoryLoading, setIsHistoryLoading] = useState(false);

  useEffect(() => {
    setStateHistory(null);
    setHistoryError("");
    if (!graph) {
      setIsHistoryLoading(false);
      return;
    }
    let canceled = false;
    setIsHistoryLoading(true);
    getTurnStateHistory(graph.conversation_id, graph.turn_id)
      .then((history) => {
        if (!canceled) {
          setStateHistory(history);
        }
      })
      .catch((currentError) => {
        if (!canceled) {
          setHistoryError(
            currentError instanceof Error ? currentError.message : "读取 checkpoint history 失败",
          );
        }
      })
      .finally(() => {
        if (!canceled) {
          setIsHistoryLoading(false);
        }
      });
    return () => {
      canceled = true;
    };
  }, [graph?.conversation_id, graph?.turn_id]);

  return (
    <ChatGraphWorkspace
      graph={graph}
      historyError={historyError}
      isClosing={isClosing}
      isHistoryLoading={isHistoryLoading}
      isLoading={isLoading}
      onClose={onClose}
      stateHistory={stateHistory}
    />
  );
}
