import { useRef, useEffect } from "react";
import { useApp } from "../context";
import StatusStrip from "./StatusStrip";
import ChatMessage from "./ChatMessage";
import ChatInput from "./ChatInput";
import EmptyState from "./EmptyState";

export default function ChatArea() {
  const { messages } = useApp();
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const hasMessages = messages.length > 0;

  return (
    <div className="chat-area">
      {hasMessages && <StatusStrip />}
      <div className="messages">
        {hasMessages ? (
          <>
            {messages.map((msg) => (
              <ChatMessage key={msg.id} message={msg} />
            ))}
            <div ref={messagesEndRef} />
          </>
        ) : (
          <EmptyState />
        )}
      </div>
      <ChatInput />
    </div>
  );
}
